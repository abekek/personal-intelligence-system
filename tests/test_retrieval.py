from pathlib import Path

from pis import ids
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from pis.retrieval.search import get_conversation, search_exact, search_fts
from tests.test_normalize_claude_code import turn_event


def seeded(db):
    ingest_events(db, [turn_event()], PolicyEngine.load(Path("config")))
    return ids.conversation_id("claude_code", "sess-abc")


def test_exact_search_finds_message_and_reports_evidence(db):
    conv_id = seeded(db)
    hits = search_exact(db, "retry helper")
    assert hits, "expected at least one exact hit"
    hit = hits[0]
    assert hit.conversation_id == conv_id
    assert hit.event_id and hit.event_id.startswith("evt_")
    assert "retry helper" in hit.snippet


def test_fts_search_ranks_message(db):
    seeded(db)
    hits = search_fts(db, "retry backoff")
    assert hits and hits[0].kind in {"message", "turn"}
    assert hits[0].score > 0


def test_search_misses_return_empty(db):
    seeded(db)
    assert search_fts(db, "zebra quantum") == []


def test_get_conversation_shape(db):
    conv_id = seeded(db)
    result = get_conversation(db, conv_id)
    assert result["conversation"]["id"] == conv_id
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]
    assert result["messages"][0]["text"] == "Add a retry helper"
    assert [t["tool_name"] for t in result["tool_events"]] == ["Edit"]
    assert get_conversation(db, "conv_missing") is None
