"""pis CLI: historical import (spec §30.12).

  pis import inspect <root>
  pis import run <root> [--mode bootstrap|incremental] [--api-url URL] [--token TOKEN] [--local]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

from pis.importers.claude_code import build_events_for_transcript, import_root, iter_transcripts


def _cmd_inspect(args: argparse.Namespace) -> int:
    if args.adapter in ("claude-export", "chatgpt-export"):
        if args.adapter == "claude-export":
            from pis.importers.claude_export import build_events_for_export_file
        else:
            from pis.importers.chatgpt_export import build_events_for_export_file
        events, warnings = build_events_for_export_file(Path(args.root))
        print(json.dumps({"source": args.root, "events": len(events),
                          "warnings": {k: v for k, v in warnings.items() if v}},
                         indent=2))
        return 0
    total_transcripts = 0
    total_events = 0
    warnings: dict = {}
    for transcript in iter_transcripts(Path(args.root)):
        total_transcripts += 1
        events, w = build_events_for_transcript(transcript)
        total_events += len(events)
        for key, count in w.items():
            if count:
                warnings[key] = warnings.get(key, 0) + count
    print(json.dumps({"root": args.root, "transcripts": total_transcripts,
                      "turn_events": total_events, "warnings": warnings}, indent=2))
    return 0


def _api_sender(api_url: str, token: str):
    def sender(events: list[dict]) -> list[dict]:
        response = httpx.post(
            f"{api_url}/v1/events", json={"events": events},
            headers={"Authorization": f"Bearer {token}"}, timeout=60.0,
        )
        response.raise_for_status()
        return response.json()
    return sender


def _local_sender():
    from pis.config import Settings
    from pis.db.engine import get_engine, make_session_factory
    from pis.ingest.service import ingest_events
    from pis.policy.engine import PolicyEngine
    from pis.schemas.events import CanonicalEvent
    import pis.normalize.chat  # noqa: F401
    import pis.normalize.claude_code  # noqa: F401

    settings = Settings()
    session_factory = make_session_factory(get_engine(settings.database_url))
    policy = PolicyEngine.load(settings.config_dir)

    def sender(events: list[dict]) -> list:
        with session_factory() as db:
            return ingest_events(
                db, [CanonicalEvent.model_validate(e) for e in events], policy
            )
    return sender


def _cmd_run(args: argparse.Namespace) -> int:
    if args.local:
        sender = _local_sender()
    else:
        if not (args.api_url and args.token):
            print("--api-url and --token required (or use --local)", file=sys.stderr)
            return 2
        sender = _api_sender(args.api_url, args.token)

    if args.adapter == "claude-export":
        from pis.importers.claude_export import collect_attachment_documents, import_claude_export
        manifest = import_claude_export(Path(args.root), sender)
        if args.api_url and args.token:
            docs = collect_attachment_documents(Path(args.root))
            counts = {"created": 0, "duplicate": 0, "unsupported": 0, "errors": 0}
            for doc in docs:
                try:
                    response = httpx.post(
                        f"{args.api_url}/v1/artifacts",
                        params={"filename": doc["filename"],
                                "conversation_uuid": doc["conversation_uuid"],
                                "provider": "claude"},
                        content=doc["content"].encode("utf-8"),
                        headers={"Authorization": f"Bearer {args.token}"},
                        timeout=120.0,
                    )
                    response.raise_for_status()
                    status = response.json()["status"]
                    counts[status] = counts.get(status, 0) + 1
                except Exception:
                    counts["errors"] += 1
            manifest["attachment_documents"] = counts
    elif args.adapter == "chatgpt-export":
        from pis.importers.chatgpt_export import import_chatgpt_export
        manifest = import_chatgpt_export(Path(args.root), sender)
    else:
        manifest = import_root(Path(args.root), sender)
    manifest["mode"] = args.mode
    manifest["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    out_dir = Path("var/import-manifests")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"imp_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))
    print(f"manifest: {out_path}", file=sys.stderr)
    return 0


SCAN_SUFFIXES = {".pdf", ".docx", ".md", ".txt", ".tex", ".csv"}


def _cmd_artifacts_scan(args: argparse.Namespace) -> int:
    if not (args.api_url and args.token):
        print("--api-url and --token required", file=sys.stderr)
        return 2
    root = Path(args.root).expanduser()
    manifest = {"root": str(root), "created": 0, "duplicate": 0,
                "unsupported": 0, "errors": 0, "skipped_large": 0}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        size = path.stat().st_size
        if size == 0:
            manifest["skipped_empty"] = manifest.get("skipped_empty", 0) + 1
            continue
        if size > 25_000_000:
            manifest["skipped_large"] += 1
            continue
        last_exc = None
        for attempt in range(3):  # transient 5xx/timeouts under load
            try:
                response = httpx.post(
                    f"{args.api_url}/v1/artifacts", params={"filename": path.name},
                    content=path.read_bytes(),
                    headers={"Authorization": f"Bearer {args.token}"}, timeout=120.0,
                )
                response.raise_for_status()
                status = response.json()["status"]
                manifest[status] = manifest.get(status, 0) + 1
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(3 * (attempt + 1))
        if last_exc is not None:
            manifest["errors"] += 1
            print(f"error: {path}: {last_exc}", file=sys.stderr)
    print(json.dumps(manifest, indent=2))
    return 0


def _cmd_artifacts_resolve(args: argparse.Namespace) -> int:
    """Match claude.ai export binary references to stored artifacts."""
    from pis.importers.claude_export import collect_binary_references
    references, warnings = collect_binary_references(Path(args.export))
    totals = {"resolved": 0, "unresolved": 0, "already_resolved": 0, "upgraded": 0}
    for i in range(0, len(references), 500):
        response = httpx.post(
            f"{args.api_url}/v1/artifacts/resolve-references",
            json={"provider": "claude", "references": references[i : i + 500]},
            headers={"Authorization": f"Bearer {args.token}"}, timeout=115.0,
        )
        response.raise_for_status()
        for key, value in response.json().items():
            totals[key] = totals.get(key, 0) + value
    print(json.dumps({"references": len(references), **totals,
                      "warnings": warnings}, indent=2))
    return 0


def _cmd_import_extras(args: argparse.Namespace) -> int:
    """Ingest projects/ docs and memories.json from a claude.ai export."""
    from pis.importers.claude_export import collect_export_extras
    manifest = {"documents": 0, "created": 0, "duplicate": 0, "errors": 0}
    for doc in collect_export_extras(Path(args.export)):
        manifest["documents"] += 1
        try:
            response = httpx.post(
                f"{args.api_url}/v1/artifacts",
                params={"filename": doc["filename"]},
                content=doc["content"].encode(),
                headers={"Authorization": f"Bearer {args.token}"}, timeout=115.0,
            )
            response.raise_for_status()
            status = response.json()["status"]
            manifest[status] = manifest.get(status, 0) + 1
        except Exception as exc:
            manifest["errors"] += 1
            print(f"error: {doc['filename']}: {exc}", file=sys.stderr)
    print(json.dumps(manifest, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="pis")
    sub = parser.add_subparsers(dest="command", required=True)

    artifacts = sub.add_parser("artifacts", help="document ingestion")
    artifacts_sub = artifacts.add_subparsers(dest="artifacts_command", required=True)
    scan = artifacts_sub.add_parser("scan", help="scan a directory of documents")
    scan.add_argument("root")
    scan.add_argument("--api-url")
    scan.add_argument("--token")
    scan.set_defaults(fn=_cmd_artifacts_scan)
    resolve = artifacts_sub.add_parser(
        "resolve", help="match export binary refs to stored artifacts")
    resolve.add_argument("export")
    resolve.add_argument("--api-url")
    resolve.add_argument("--token")
    resolve.set_defaults(fn=_cmd_artifacts_resolve)

    imp = sub.add_parser("import", help="historical import")
    imp_sub = imp.add_subparsers(dest="import_command", required=True)

    extras = imp_sub.add_parser(
        "extras", help="ingest projects/ + memories.json from a claude.ai export")
    extras.add_argument("export")
    extras.add_argument("--api-url")
    extras.add_argument("--token")
    extras.set_defaults(fn=_cmd_import_extras)

    inspect = imp_sub.add_parser("inspect", help="dry-run inspection")
    inspect.add_argument("root")
    inspect.add_argument("--adapter", choices=["claude-code", "claude-export", "chatgpt-export"],
                         default="claude-code")
    inspect.set_defaults(fn=_cmd_inspect)

    run = imp_sub.add_parser("run", help="run import")
    run.add_argument("root")
    run.add_argument("--adapter", choices=["claude-code", "claude-export", "chatgpt-export"],
                     default="claude-code")
    run.add_argument("--mode", choices=["bootstrap", "incremental"], default="bootstrap")
    run.add_argument("--api-url")
    run.add_argument("--token")
    run.add_argument("--local", action="store_true")
    run.set_defaults(fn=_cmd_run)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
