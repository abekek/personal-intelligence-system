"""ChatGPT account export importer (spec §30.3).

conversations.json holds a node graph per conversation (mapping of node_id ->
{message, parent, children}). All user/assistant text nodes are imported
(branches included), ordered by create_time; non-text parts and other roles
are counted, not silently dropped.
"""
from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pis.security.secrets import redact_text

ROLES = {"user", "assistant"}


def _load_conversations(path: Path) -> list[dict]:
    path = Path(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith("conversations.json")]
            if not names:
                raise FileNotFoundError("conversations.json not in export zip")
            return json.loads(archive.read(names[0]))
    if path.is_dir():
        return json.loads((path / "conversations.json").read_text(encoding="utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _text_of_parts(content: dict, warnings: dict) -> str:
    parts = (content or {}).get("parts") or []
    texts = []
    for part in parts:
        if isinstance(part, str):
            texts.append(part)
        else:
            warnings["nontext_parts_skipped"] = warnings.get("nontext_parts_skipped", 0) + 1
    return "\n".join(t for t in texts if t).strip()


def build_events_for_export_file(path: Path) -> tuple[list[dict], dict]:
    warnings: dict = {"skipped_roles": 0, "empty_messages_skipped": 0}
    events: list[dict] = []
    for conv in _load_conversations(path):
        conv_id = conv.get("id") or conv.get("conversation_id") or "unknown"
        title = conv.get("title") or None
        nodes = []
        for node in (conv.get("mapping") or {}).values():
            message = (node or {}).get("message")
            if not message:
                continue
            role = ((message.get("author") or {}).get("role"))
            if role not in ROLES:
                warnings["skipped_roles"] += 1
                continue
            text = _text_of_parts(message.get("content"), warnings)
            if not text:
                warnings["empty_messages_skipped"] += 1
                continue
            created = message.get("create_time") or conv.get("create_time") or 0
            nodes.append((created, message.get("id"), role, text))
        nodes.sort(key=lambda n: (n[0] or 0, n[1] or ""))
        for created, message_id, role, text in nodes:
            events.append({
                "event_type": "conversation.message.completed",
                "provider": "chatgpt",
                "provider_conversation_id": conv_id,
                "provider_message_id": message_id,
                "role": role,
                "occurred_at": datetime.fromtimestamp(created, tz=timezone.utc).isoformat(),
                "capture_method": "export_import",
                "source_uri": f"https://chatgpt.com/c/{conv_id}",
                "content_parts": [{"type": "text", "text": redact_text(text)}],
                "metadata": {
                    "import_source": "chatgpt_export",
                    "conversation_title": title,
                },
            })
    return events, warnings


def import_chatgpt_export(path: Path, sender: Callable[[list[dict]], list],
                          batch_size: int = 50) -> dict:
    events, warnings = build_events_for_export_file(path)
    conversations = {e["provider_conversation_id"] for e in events}
    manifest = {
        "source": str(path), "adapter": "chatgpt-export",
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
