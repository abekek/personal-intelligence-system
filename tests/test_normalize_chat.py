from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

import pis.normalize.chat  # noqa: F401  (registers normalizer)
from pis import ids
from pis.db.models import Conversation, Message, MessageRevision
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from pis.retrieval.search import search_fts
from pis.schemas.events import CanonicalEvent, ContentPart, EventType

OCC = datetime(2026, 7, 18, 20, 0, 0, tzinfo=timezone.utc)


def note_event(text="Decided to use RDS gp3 storage for the ledger", key="claude-ai/notes"):
    return CanonicalEvent(
        event_type=EventType.MESSAGE_COMPLETED,
        provider="claude",
        provider_conversation_id=key,
        role="assistant",
        occurred_at=OCC,
        capture_method="manual",
        content_parts=[ContentPart(type="text", text=text)],
        metadata={"source": "mcp_capture"},
    )


def policy():
    return PolicyEngine.load(Path("config"))


def test_note_projects_message_and_is_searchable(db):
    ingest_events(db, [note_event()], policy())

    conv = db.get(Conversation, ids.conversation_id("claude", "claude-ai/notes"))
    assert conv is not None and conv.provider == "claude"

    messages = db.scalars(sa.select(Message)).all()
    assert len(messages) == 1 and messages[0].role == "assistant"
    revs = db.scalars(sa.select(MessageRevision)).all()
    assert revs[0].text_content.startswith("Decided to use RDS")

    hits = search_fts(db, "gp3 storage ledger")
    assert hits and hits[0].event_id


def test_notes_append_positions_and_replay_dedupes(db):
    ingest_events(db, [note_event()], policy())
    ingest_events(db, [note_event(text="Second note about deploys")], policy())
    ingest_events(db, [note_event()], policy())  # replay -> duplicate
    positions = sorted(db.scalars(sa.select(Message.position)).all())
    assert positions == [0, 1]
