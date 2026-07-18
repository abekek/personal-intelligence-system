from pathlib import Path

import sqlalchemy as sa

import pis.normalize.chat  # noqa: F401
from pis import ids
from pis.db.models import Conversation, Event, Message
from pis.importers.claude_export import (
    build_events_for_export_file,
    collect_attachment_documents,
    import_claude_export,
)
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from pis.schemas.events import CanonicalEvent

FIXTURE = Path("tests/fixtures/claude_export/conversations.json")


def test_build_events_shape_and_redaction():
    events, warnings = build_events_for_export_file(FIXTURE)
    assert len(events) == 3  # empty assistant message skipped

    first = CanonicalEvent.model_validate(events[0])
    assert first.provider == "claude"
    assert first.provider_conversation_id == "11111111-aaaa-bbbb-cccc-000000000001"
    assert first.provider_message_id == "msg-0001"
    assert first.role == "user"
    assert first.capture_method == "export_import"
    assert first.metadata["conversation_title"] == "Paper revision strategy"
    assert first.source_uri.endswith("11111111-aaaa-bbbb-cccc-000000000001")

    secret_msg = CanonicalEvent.model_validate(events[2])
    assert "topsecret99" not in secret_msg.content_parts[0].text
    assert "[REDACTED:password_assignment]" in secret_msg.content_parts[0].text

    assert warnings["empty_messages_skipped"] == 1
    assert warnings["attachments_text_ingested"] == 1
    attachment_part = secret_msg.content_parts[1]
    assert attachment_part.type == "attachment"
    assert "rotate credentials quarterly" in attachment_part.text
    assert attachment_part.metadata["file_name"] == "notes.pdf"


def test_collect_attachment_documents():
    docs = collect_attachment_documents(FIXTURE)
    assert len(docs) == 1
    assert docs[0]["filename"] == "notes.pdf"
    assert "rotate credentials" in docs[0]["content"]
    assert docs[0]["conversation_uuid"].endswith("0002")


def test_import_is_idempotent_and_titles_conversations(db):
    policy = PolicyEngine.load(Path("config"))

    def sender(events):
        return ingest_events(db, [CanonicalEvent.model_validate(e) for e in events], policy)

    manifest = import_claude_export(FIXTURE, sender)
    assert manifest["conversations_seen"] == 2
    assert manifest["created"] == 3

    conv = db.get(Conversation, ids.conversation_id(
        "claude", "11111111-aaaa-bbbb-cccc-000000000001"))
    assert conv.title == "Paper revision strategy"
    roles = db.scalars(
        sa.select(Message.role).where(Message.conversation_id == conv.id)
        .order_by(Message.position)).all()
    assert roles == ["user", "assistant"]

    again = import_claude_export(FIXTURE, sender)
    assert again["created"] == 0 and again["duplicate"] == 3
    assert db.scalar(sa.select(sa.func.count()).select_from(Event)) == 3
