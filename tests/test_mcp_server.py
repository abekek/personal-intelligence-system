import json
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from pis.api.app import create_app
from pis.config import Settings
from pis.db.engine import get_engine, make_session_factory
from pis.db.models import Event
from pis.mcp_server.app import build_mcp
from pis.policy.engine import PolicyEngine
from tests.conftest import TEST_DB_URL
from tests.test_normalize_claude_code import turn_event
from tests.test_oauth import full_dance

SETTINGS = Settings(
    database_url=TEST_DB_URL,
    public_url="https://pis.example.com",
    oauth_passcode="test-passcode-123",
)


def tools_of(engine):
    session_factory = make_session_factory(engine)
    policy = PolicyEngine.load(Path("config"))
    mcp = build_mcp(SETTINGS, session_factory, policy)
    # FastMCP registers plain functions; fetch them for direct calls
    return {t.name: t.fn for t in mcp._tool_manager.list_tools()}


def seed(db):
    from pis.ingest.service import ingest_events
    ingest_events(db, [turn_event()], PolicyEngine.load(Path("config")))


def test_kb_search_and_get_conversation(engine, db):
    seed(db)
    tools = tools_of(engine)
    hits = tools["kb_search"]("retry helper", mode="exact")
    assert hits and hits[0]["event_id"].startswith("evt_")
    conv = tools["kb_get_conversation"](hits[0]["conversation_id"])
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]


def test_kb_capture_note_roundtrip(engine, db):
    tools = tools_of(engine)
    result = tools["kb_capture_note"](
        "Decision: skip browser extension; capture via MCP write tool",
        conversation_key="pis/roadmap",
    )
    assert result["status"] == "created"
    hits = tools["kb_search"]("browser extension MCP write")
    assert hits and hits[0]["conversation_id"] == result["conversation_id"]
    # ledger event exists with manual capture method
    row = db.get(Event, result["event_id"])
    assert row is not None and row.capture_method == "manual"


def test_kb_capture_document(engine, db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pis.storage.s3.make_object_store",
        lambda settings: __import__("pis.storage.objects", fromlist=["ObjectStore"])
        .ObjectStore(tmp_path),
    )
    tools = tools_of(engine)
    result = tools["kb_capture_document"](
        "letter-strategy.md", "Recommendation letters: ask research collaborators first.",
        note="uploaded in claude.ai",
    )
    assert result["status"] == "created" and result["chunks"] == 1
    hits = tools["kb_search"]("recommendation letters collaborators", mode="fts")
    assert any(h["kind"] == "document" for h in hits)


def test_kb_recent_activity(engine, db):
    seed(db)
    tools = tools_of(engine)
    recent = tools["kb_recent_activity"](limit=5)
    assert recent and recent[0]["conversation_id"].startswith("conv_")


def test_mcp_endpoint_requires_token_and_serves_metadata(engine, db):
    with TestClient(create_app(SETTINGS)) as client:
        meta = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert meta["authorization_servers"] == ["https://pis.example.com"]

        # exact /mcp must NOT redirect (MCP clients drop auth on redirects)
        r = client.post("/mcp", json={}, follow_redirects=False)
        assert r.status_code == 401
        assert "resource_metadata" in r.headers["www-authenticate"]

        _, _, token = full_dance(client)
        access = token.json()["access_token"]
        init = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {access}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        assert init.status_code == 200
        assert "pis-knowledge" in init.text
