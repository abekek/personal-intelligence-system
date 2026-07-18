from pathlib import Path

import sqlalchemy as sa

import pis.normalize.chat  # noqa: F401
from pis import ids
from pis.db.models import Conversation, Event, Message
from pis.importers.chatgpt_export import build_events_for_export_file, import_chatgpt_export
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from pis.schemas.events import CanonicalEvent

FIXTURE = Path("tests/fixtures/chatgpt_export/conversations.json")


def test_build_events_traverses_mapping():
    events, warnings = build_events_for_export_file(FIXTURE)
    assert len(events) == 3  # system + empty + image-only skipped/counted

    first = CanonicalEvent.model_validate(events[0])
    assert first.provider == "chatgpt"
    assert first.provider_conversation_id == "conv-gpt-0001"
    assert first.provider_message_id == "gptmsg-1"
    assert first.role == "user"
    assert first.occurred_at.year == 2026
    assert first.metadata["conversation_title"] == "Benchmark ideas"
    assert first.source_uri == "https://chatgpt.com/c/conv-gpt-0001"

    redacted = CanonicalEvent.model_validate(events[2])
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted.content_parts[0].text

    assert warnings["skipped_roles"] == 1        # system message
    assert warnings["empty_messages_skipped"] == 1  # image-only assistant msg


def test_import_idempotent(db):
    policy = PolicyEngine.load(Path("config"))

    def sender(events):
        return ingest_events(db, [CanonicalEvent.model_validate(e) for e in events], policy)

    manifest = import_chatgpt_export(FIXTURE, sender)
    assert manifest["conversations_seen"] == 2
    assert manifest["created"] == 3

    conv = db.get(Conversation, ids.conversation_id("chatgpt", "conv-gpt-0001"))
    assert conv.title == "Benchmark ideas"
    roles = db.scalars(
        sa.select(Message.role).where(Message.conversation_id == conv.id)
        .order_by(Message.position)).all()
    assert roles == ["user", "assistant"]

    again = import_chatgpt_export(FIXTURE, sender)
    assert again["created"] == 0 and again["duplicate"] == 3
    assert db.scalar(sa.select(sa.func.count()).select_from(Event)) == 3
