from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

import pis.normalize.claude_code  # noqa: F401  (registers normalizer)
from pis import ids
from pis.db.models import CodeSession, Conversation, Message, MessageRevision, ToolEvent, Turn
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from pis.schemas.events import CanonicalEvent, ContentPart, EventType

OCC = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def turn_event(prompt="Add a retry helper", answer="Added retry helper with backoff."):
    return CanonicalEvent(
        event_type=EventType.CC_TURN_COMPLETED,
        provider="claude_code",
        provider_conversation_id="sess-abc",
        occurred_at=OCC,
        capture_method="claude_code_hook",
        content_parts=[
            ContentPart(type="text", text=prompt, metadata={"role": "user"}),
            ContentPart(type="text", text=answer, metadata={"role": "assistant"}),
        ],
        metadata={
            "session_id": "sess-abc",
            "cwd": "/Users/alibek/demo",
            "repo_root": "/Users/alibek/demo",
            "git_remote": "git@github.com:abekek/demo.git",
            "branch": "main",
            "head_commit": "a" * 40,
            "changed_files": ["src/utils.py"],
            "tool_uses": [{"tool_name": "Edit", "summary": {"file_path": "src/utils.py"}}],
        },
    )


def test_turn_event_projects_full_graph(db):
    ingest_events(db, [turn_event()], PolicyEngine.load(Path("config")))

    session = db.get(CodeSession, "sess-abc")
    assert session.branch == "main"

    conv = db.get(Conversation, ids.conversation_id("claude_code", "sess-abc"))
    assert conv is not None

    turn = db.get(Turn, ids.turn_id("sess-abc", 0))
    assert turn.changed_files == ["src/utils.py"]

    messages = db.scalars(
        sa.select(Message).where(Message.conversation_id == conv.id).order_by(Message.position)
    ).all()
    assert [m.role for m in messages] == ["user", "assistant"]

    revs = db.scalars(sa.select(MessageRevision)).all()
    assert {r.text_content for r in revs} == {"Add a retry helper", "Added retry helper with backoff."}
    assert all(r.event_id for r in revs)

    tools = db.scalars(sa.select(ToolEvent)).all()
    assert [t.tool_name for t in tools] == ["Edit"]
    assert tools[0].turn_id == turn.id


def test_replay_is_idempotent(db):
    policy = PolicyEngine.load(Path("config"))
    ingest_events(db, [turn_event()], policy)
    ingest_events(db, [turn_event()], policy)  # same content -> duplicate, not re-normalized
    assert db.scalar(sa.select(sa.func.count()).select_from(Turn)) == 1
    assert db.scalar(sa.select(sa.func.count()).select_from(Message)) == 2


def test_second_turn_appends(db):
    policy = PolicyEngine.load(Path("config"))
    ingest_events(db, [turn_event()], policy)
    ingest_events(db, [turn_event(prompt="Now add tests", answer="Added tests.")], policy)
    assert db.scalar(sa.select(sa.func.count()).select_from(Turn)) == 2
    positions = sorted(db.scalars(sa.select(Message.position)).all())
    assert positions == [0, 1, 2, 3]
