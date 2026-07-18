from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from pis.api.app import create_app
from pis.config import Settings
from pis.db.models import AuditLog, Event
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from pis.schemas.events import CanonicalEvent, ContentPart, EventType

OCC = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def make_event(text="hello world", **kw):
    defaults = dict(
        event_type=EventType.CC_TURN_COMPLETED,
        provider="claude_code",
        provider_conversation_id="sess-1",
        occurred_at=OCC,
        capture_method="claude_code_hook",
        content_parts=[ContentPart(type="text", text=text)],
        metadata={"session_id": "sess-1"},
    )
    defaults.update(kw)
    return CanonicalEvent(**defaults)


def policy():
    return PolicyEngine.load(Path("config"))


def test_ingest_creates_then_deduplicates(db):
    ev = make_event()
    assert [r.status for r in ingest_events(db, [ev], policy())] == ["created"]
    assert [r.status for r in ingest_events(db, [ev], policy())] == ["duplicate"]
    replay = make_event()  # new event_id, same content -> same hash
    assert [r.status for r in ingest_events(db, [replay], policy())] == ["duplicate"]
    assert db.scalar(sa.select(sa.func.count()).select_from(Event)) == 1


def test_ingest_rejects_denied_repo_and_audits(db):
    ev = make_event(metadata={"git_remote": "git@github.com:employer-org/x.git"})
    [res] = ingest_events(db, [ev], policy())
    assert (res.status, res.reason) == ("rejected", "denied_repository")
    assert db.scalar(sa.select(sa.func.count()).select_from(Event)) == 0
    actions = db.scalars(sa.select(AuditLog.action)).all()
    assert "ingest.rejected" in actions


def test_ingest_rejects_secret_content(db):
    [res] = ingest_events(db, [make_event(text="key AKIAIOSFODNN7EXAMPLE")], policy())
    assert res.status == "rejected"
    assert res.reason.startswith("secret_detected:")


def test_created_events_are_audited(db):
    ingest_events(db, [make_event()], policy())
    assert "ingest.created" in db.scalars(sa.select(AuditLog.action)).all()


def _client():
    settings = Settings(
        database_url="postgresql+psycopg://pis:pis@127.0.0.1:5433/pis_test"
    )
    return TestClient(create_app(settings)), settings


def test_api_requires_token(engine, db):
    client, _ = _client()
    r = client.post("/v1/events", json={"events": []})
    assert r.status_code == 401


def test_api_ingests_batch(engine, db):
    client, settings = _client()
    body = {"events": [make_event().model_dump(mode="json")]}
    r = client.post("/v1/events", json=body,
                    headers={"Authorization": f"Bearer {settings.ingest_token}"})
    assert r.status_code == 200
    assert r.json()[0]["status"] == "created"
