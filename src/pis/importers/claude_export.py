"""Claude.ai account export importer (spec §30.4).

Accepts the export zip, an extracted directory, or conversations.json
directly. Messages become conversation.message.completed events through the
same idempotent pipeline; the chat normalizer projects them. Attachments are
counted as unresolved references (binaries handled by the artifact phase).
"""
from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from pathlib import Path

from pis.security.secrets import redact_text


def _load_conversations(path: Path, warnings: dict) -> list[dict]:
    path = Path(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith("conversations.json")]
            if not names:
                raise FileNotFoundError("conversations.json not in export zip")
            data = json.loads(archive.read(names[0]))
    elif path.is_dir():
        data = json.loads((path / "conversations.json").read_text(encoding="utf-8"))
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        warnings["unsupported_records"] = warnings.get("unsupported_records", 0) + 1
        return []
    return data


def _message_text(message: dict) -> str:
    text = message.get("text") or ""
    if not text:
        parts = message.get("content") or []
        if isinstance(parts, list):
            text = "\n".join(
                p.get("text", "") for p in parts
                if isinstance(p, dict) and p.get("type") == "text"
            )
    return text.strip()


ROLE_MAP = {"human": "user", "assistant": "assistant"}


def build_events_for_export_file(path: Path) -> tuple[list[dict], dict]:
    warnings: dict = {"empty_messages_skipped": 0, "attachments_unresolved": 0,
                      "unsupported_senders": 0}
    events: list[dict] = []
    for conv in _load_conversations(path, warnings):
        conv_id = conv.get("uuid") or "unknown"
        title = conv.get("name") or None
        for message in conv.get("chat_messages") or []:
            role = ROLE_MAP.get(message.get("sender"))
            if role is None:
                warnings["unsupported_senders"] += 1
                continue
            attachment_parts = []
            for attachment in (message.get("attachments") or []):
                extracted = (attachment or {}).get("extracted_content") or ""
                if extracted.strip():
                    attachment_parts.append({
                        "type": "attachment",
                        "text": redact_text(extracted.strip()),
                        "metadata": {"file_name": attachment.get("file_name")},
                    })
                    warnings["attachments_text_ingested"] = (
                        warnings.get("attachments_text_ingested", 0) + 1)
                else:
                    warnings["attachments_unresolved"] += 1
            warnings["attachments_unresolved"] += len(message.get("files") or [])
            text = _message_text(message)
            if not text and not attachment_parts:
                warnings["empty_messages_skipped"] += 1
                continue
            occurred_at = message.get("created_at") or conv.get("created_at")
            if not occurred_at:
                warnings["unsupported_records"] = warnings.get("unsupported_records", 0) + 1
                continue
            events.append({
                "event_type": "conversation.message.completed",
                "provider": "claude",
                "provider_conversation_id": conv_id,
                "provider_message_id": message.get("uuid"),
                "role": role,
                "occurred_at": occurred_at,
                "capture_method": "export_import",
                "source_uri": f"https://claude.ai/chat/{conv_id}",
                "content_parts": (
                    [{"type": "text", "text": redact_text(text)}] if text else []
                ) + attachment_parts,
                "metadata": {
                    "import_source": "claude_export",
                    "conversation_title": title,
                },
            })
    return events, warnings


def collect_attachment_documents(path: Path) -> list[dict]:
    """Attachments with extracted text, as standalone documents for the
    artifact pipeline: [{filename, content, conversation_uuid}]."""
    warnings: dict = {}
    documents = []
    for conv in _load_conversations(Path(path), warnings):
        conv_id = conv.get("uuid") or "unknown"
        for message in conv.get("chat_messages") or []:
            for attachment in (message.get("attachments") or []):
                extracted = (attachment or {}).get("extracted_content") or ""
                if extracted.strip():
                    documents.append({
                        "filename": attachment.get("file_name") or "attachment.txt",
                        "content": extracted.strip(),
                        "conversation_uuid": conv_id,
                    })
    return documents


def collect_binary_references(path: Path) -> tuple[list[dict], dict]:
    """Binary file references from export messages: the export carries only
    {file_uuid, file_name}, never bytes, so these resolve against artifacts
    ingested from disk scans. Returns unique (conversation, name) pairs."""
    warnings: dict = {"nameless_refs_skipped": 0}
    seen: set[tuple[str, str]] = set()
    references: list[dict] = []
    for conv in _load_conversations(Path(path), warnings):
        conv_id = conv.get("uuid") or "unknown"
        for message in conv.get("chat_messages") or []:
            for file_ref in message.get("files") or []:
                name = (file_ref.get("file_name") or "").strip()
                if not name:
                    warnings["nameless_refs_skipped"] += 1
                    continue
                if (conv_id, name) in seen:
                    continue
                seen.add((conv_id, name))
                references.append({"conversation_uuid": conv_id, "file_name": name})
    return references, warnings


def collect_export_extras(path: Path) -> list[dict]:
    """Project docs and account-level memory from the export zip/dir, as
    documents for the artifact pipeline: [{filename, content}].

    memories.json is Claude's own summary of the user (agent-authored) —
    ingested as documents it stays searchable but is never mined into
    memories, so it cannot launder into primary-evidence facts."""
    path = Path(path)

    def _read(name: str) -> bytes | None:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                matches = [n for n in archive.namelist() if n == name
                           or n.endswith("/" + name)]
                return archive.read(matches[0]) if matches else None
        candidate = path / name
        return candidate.read_bytes() if candidate.is_file() else None

    def _project_files() -> list[tuple[str, bytes]]:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                return [(n, archive.read(n)) for n in archive.namelist()
                        if "projects/" in n and n.endswith(".json")]
        return [(str(p), p.read_bytes())
                for p in sorted((path / "projects").glob("*.json"))] \
            if (path / "projects").is_dir() else []

    documents: list[dict] = []
    for _, raw in _project_files():
        project = json.loads(raw)
        name = project.get("name") or project.get("uuid") or "project"
        description = (project.get("description") or "").strip()
        template = (project.get("prompt_template") or "").strip()
        overview = "\n\n".join(part for part in (
            f"# Claude.ai project: {name}", description,
            f"## Prompt template\n{template}" if template else "") if part)
        if description or template:
            documents.append({
                "filename": f"claude-project--{name}--overview.md",
                "content": overview})
        for doc in project.get("docs") or []:
            content = (doc.get("content") or "").strip()
            if content:
                documents.append({
                    "filename": f"claude-project--{name}--{doc.get('filename') or 'doc.md'}",
                    "content": content})

    raw = _read("memories.json")
    if raw:
        for account in json.loads(raw):
            conv_memory = (account.get("conversations_memory") or "").strip()
            if conv_memory:
                documents.append({
                    "filename": "claude-memory--conversations.md",
                    "content": conv_memory})
            for project_uuid, text in (account.get("project_memories") or {}).items():
                if (text or "").strip():
                    documents.append({
                        "filename": f"claude-memory--project-{project_uuid}.md",
                        "content": text.strip()})
            for entry in account.get("memory_files") or []:
                content = (entry.get("content") or "").strip()
                if content:
                    slug = (entry.get("path") or "file").strip("/").replace("/", "--")
                    documents.append({
                        "filename": f"claude-memory--{slug}",
                        "content": content})
    return documents


def import_claude_export(path: Path, sender: Callable[[list[dict]], list],
                         batch_size: int = 50) -> dict:
    warnings_events = build_events_for_export_file(path)
    events, warnings = warnings_events
    conversations = {e["provider_conversation_id"] for e in events}
    manifest = {
        "source": str(path), "adapter": "claude-export",
        "conversations_seen": len(conversations), "events_built": len(events),
        "created": 0, "duplicate": 0, "rejected": 0,
        "warnings": {k: v for k, v in warnings.items() if v},
    }
    for i in range(0, len(events), batch_size):
        for result in sender(events[i : i + batch_size]):
            status = getattr(result, "status", None) or result.get("status")
            if status in manifest:
                manifest[status] += 1
    return manifest
