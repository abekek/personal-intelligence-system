"""Spec §26 acceptance: Stop hook -> daemon -> /v1/events -> ledger -> normalizer
-> exact/FTS retrieval -> evidence references source event. Then GitHub push ->
session link. All inputs replayed to prove idempotency."""
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from pis import ids
from pis.api.app import create_app
from pis.config import Settings
from pis.daemon.app import create_daemon_app
from pis.db.models import Event, Message, SessionCommitLink, ToolEvent, Turn

FIXTURE = Path("tests/fixtures/transcript_basic.jsonl")
PUSH = json.loads(Path("tests/fixtures/github_push.json").read_text())


def load_hook():
    spec = importlib.util.spec_from_file_location(
        "stop_hook", "integrations/claude-code/hooks/stop_hook.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_slice(engine, db, tmp_path):
    settings = Settings(database_url="postgresql+psycopg://pis:pis@127.0.0.1:5433/pis_test")
    api = TestClient(create_app(settings))

    def post_fn(body: dict) -> bool:
        r = api.post("/v1/events", json=body,
                     headers={"Authorization": f"Bearer {settings.ingest_token}"})
        return r.status_code == 200

    daemon = TestClient(create_daemon_app(
        Settings(daemon_outbox_path=tmp_path / "outbox.sqlite3"), post_fn))

    # 1-5: synthetic session fixture -> hook builds the turn event -> daemon
    hook = load_hook()
    turn = hook.parse_last_turn(str(FIXTURE))
    git = {"repo_root": "/r", "git_remote": "git@github.com:abekek/demo.git",
           "branch": "main", "head_commit": "a" * 40}
    event = hook.build_event({"session_id": "sess-abc", "cwd": "/r"}, git, turn)
    body = {"events": [event]}

    r = daemon.post("/v1/capture", json=body,
                    headers={"X-Capture-Token": settings.daemon_token})
    assert r.status_code == 200
    daemon.post("/v1/flush", headers={"X-Capture-Token": settings.daemon_token})

    # 6: one canonical turn with linked tool activity
    assert db.scalar(sa.select(sa.func.count()).select_from(Turn)) == 1
    assert db.scalar(sa.select(sa.func.count()).select_from(ToolEvent)) == 1

    # 7-8: replay every input; no duplicate records
    daemon.post("/v1/capture", json=body,
                headers={"X-Capture-Token": settings.daemon_token})
    daemon.post("/v1/flush", headers={"X-Capture-Token": settings.daemon_token})
    api.post("/v1/events", json=body,
             headers={"Authorization": f"Bearer {settings.ingest_token}"})
    assert db.scalar(sa.select(sa.func.count()).select_from(Turn)) == 1
    assert db.scalar(sa.select(sa.func.count()).select_from(Message)) == 2
    assert db.scalar(sa.select(sa.func.count()).select_from(Event)) == 1

    # 9-10: exact search for response text returns evidence referencing the event
    auth = {"Authorization": f"Bearer {settings.ingest_token}"}
    hits = api.get("/v1/search", params={"q": "retry helper", "mode": "exact"},
                   headers=auth).json()
    assert hits, "exact search must find the turn"
    source_event_id = db.scalar(sa.select(Event.event_id))
    assert hits[0]["event_id"] == source_event_id

    conv_id = ids.conversation_id("claude_code", "sess-abc")
    conv = api.get(f"/v1/conversations/{conv_id}", headers=auth).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]

    # Second slice: GitHub push links back to the producing session
    push_body = json.dumps(PUSH).encode()
    signature = "sha256=" + hmac.new(settings.github_webhook_secret.encode(),
                                     push_body, hashlib.sha256).hexdigest()
    r = api.post("/v1/github/webhook", content=push_body,
                 headers={"X-Hub-Signature-256": signature, "X-GitHub-Event": "push",
                          "X-GitHub-Delivery": "accept-1",
                          "Content-Type": "application/json"})
    assert r.json()["status"] == "processed"

    [link] = db.scalars(sa.select(SessionCommitLink)).all()
    assert link.session_id == "sess-abc"

    sessions = api.get(f"/v1/git/commits/{'a' * 40}/sessions", headers=auth).json()
    assert sessions[0]["session_id"] == "sess-abc"
