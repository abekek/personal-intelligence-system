"""Historical Claude Code JSONL importer (spec §30.5, §30.21).

Walks transcript roots (~/.claude/projects), splits each session transcript
into per-turn canonical events using the same contract as the live Stop hook,
redacts secrets client-side (content is preserved-redacted, not rejected),
and sends batches through the same idempotent ingestion path.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

from pis.security.secrets import redact_text

FILE_TOOLS = {"Edit", "Write", "NotebookEdit"}
KNOWN_TYPES = {"user", "assistant", "summary", "system"}


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def _is_real_user(entry: dict) -> bool:
    if entry.get("type") != "user":
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        has_result = any(b.get("type") == "tool_result" for b in content)
        has_text = any(b.get("type") == "text" for b in content)
        return has_text and not has_result
    return False


def _parse_entries(path: Path, warnings: dict) -> list[dict]:
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            warnings["parse_errors"] = warnings.get("parse_errors", 0) + 1
            continue
        if entry.get("isSidechain"):
            warnings["sidechain_skipped"] = warnings.get("sidechain_skipped", 0) + 1
            continue
        if entry.get("type") not in KNOWN_TYPES:
            warnings["unsupported_records"] = warnings.get("unsupported_records", 0) + 1
            continue
        entries.append(entry)
    return entries


def _turn_spans(entries: list[dict]) -> list[tuple[int, int]]:
    """(start, end) index spans, one per real user prompt."""
    starts = [i for i, e in enumerate(entries) if _is_real_user(e)]
    spans = []
    for j, start in enumerate(starts):
        end = starts[j + 1] if j + 1 < len(starts) else len(entries)
        spans.append((start, end))
    return spans


def build_events_for_transcript(path: Path) -> tuple[list[dict], dict]:
    warnings: dict = {"sidechain_skipped": 0, "unsupported_records": 0}
    entries = _parse_entries(Path(path), warnings)

    session_id = None
    cwd = None
    for entry in entries:
        session_id = session_id or entry.get("sessionId")
        cwd = cwd or entry.get("cwd")
    session_id = session_id or Path(path).stem

    events: list[dict] = []
    for start, end in _turn_spans(entries):
        span = entries[start:end]
        user_prompt = _text_of(span[0].get("message", {}).get("content"))
        assistant_text = ""
        tool_uses: list[dict] = []
        changed: set[str] = set()
        occurred_at = span[-1].get("timestamp") or span[0].get("timestamp")
        for entry in span:
            if entry.get("type") != "assistant":
                continue
            content = entry.get("message", {}).get("content") or []
            if not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    assistant_text = block["text"]
                elif block.get("type") == "tool_use":
                    name = block.get("name", "unknown")
                    tool_input = block.get("input") or {}
                    summary = {}
                    if tool_input.get("file_path"):
                        summary["file_path"] = tool_input["file_path"]
                    tool_uses.append({"tool_name": name, "summary": summary})
                    if name in FILE_TOOLS and tool_input.get("file_path"):
                        changed.add(tool_input["file_path"])
        if occurred_at is None:
            warnings["turns_without_timestamp"] = warnings.get("turns_without_timestamp", 0) + 1
            continue
        events.append({
            "event_type": "claude_code.turn.completed",
            "provider": "claude_code",
            "provider_conversation_id": session_id,
            "occurred_at": occurred_at,
            "capture_method": "export_import",
            "content_parts": [
                {"type": "text", "text": redact_text(user_prompt),
                 "metadata": {"role": "user"}},
                {"type": "text", "text": redact_text(assistant_text),
                 "metadata": {"role": "assistant"}},
            ],
            "metadata": {
                "session_id": session_id,
                "cwd": cwd,
                "changed_files": sorted(changed),
                "tool_uses": tool_uses,
                "import_source": "claude_code_jsonl",
            },
        })
    return events, warnings


def iter_transcripts(root: Path) -> Iterable[Path]:
    yield from sorted(Path(root).glob("**/*.jsonl"))


def import_root(root: Path, sender: Callable[[list[dict]], list], batch_size: int = 50) -> dict:
    """sender receives batches of event dicts and returns IngestResult-likes
    with .status. Returns the import manifest."""
    manifest = {
        "root": str(root), "transcripts_seen": 0, "events_built": 0,
        "created": 0, "duplicate": 0, "rejected": 0, "warnings": {},
    }
    for transcript in iter_transcripts(root):
        manifest["transcripts_seen"] += 1
        events, warnings = build_events_for_transcript(transcript)
        for key, count in warnings.items():
            if count:
                manifest["warnings"][key] = manifest["warnings"].get(key, 0) + count
        manifest["events_built"] += len(events)
        for i in range(0, len(events), batch_size):
            for result in sender(events[i : i + batch_size]):
                status = getattr(result, "status", None) or result.get("status")
                if status in manifest:
                    manifest[status] += 1
    return manifest
