import hashlib
import hmac
import json
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from pis.api.app import create_app
from pis.config import Settings
from pis.db.models import Event, GitObject, Repository
from pis.github.webhook import handle_delivery, verify_signature
from pis.policy.engine import PolicyEngine

PAYLOAD = json.loads(Path("tests/fixtures/github_push.json").read_text())
SECRET = "dev-webhook-secret"


def policy():
    return PolicyEngine.load(Path("config"))


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature():
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, sign(body))
    assert not verify_signature(SECRET, body, "sha256=" + "0" * 64)
    assert not verify_signature(SECRET, body, None)


def test_push_creates_ledger_event_and_git_objects(db):
    result = handle_delivery(db, policy(), "delivery-1", "push", PAYLOAD)
    assert result.status == "processed"
    assert len(result.created_commits) == 1

    repo = db.scalar(sa.select(Repository).where(Repository.full_name == "abekek/demo"))
    assert repo is not None

    commit = db.get(GitObject, result.created_commits[0])
    assert commit.object_key == "a" * 40
    assert commit.files == ["src/utils.py"]
    assert commit.ref == "main"

    events = db.scalars(sa.select(Event.event_type)).all()
    assert "github.push" in events


def test_redelivery_is_duplicate(db):
    handle_delivery(db, policy(), "delivery-1", "push", PAYLOAD)
    result = handle_delivery(db, policy(), "delivery-1", "push", PAYLOAD)
    assert result.status == "duplicate"
    assert db.scalar(sa.select(sa.func.count()).select_from(GitObject)) == 1


def test_denied_repo_rejected(db):
    denied = dict(PAYLOAD, repository={"full_name": "employer-org/internal"})
    result = handle_delivery(db, policy(), "delivery-2", "push", denied)
    assert result.status == "rejected"
    assert db.scalar(sa.select(sa.func.count()).select_from(GitObject)) == 0


def test_non_push_ignored(db):
    assert handle_delivery(db, policy(), "delivery-3", "star", {}).status == "ignored"


def test_webhook_endpoint_validates_signature(engine, db):
    settings = Settings(database_url="postgresql+psycopg://pis:pis@127.0.0.1:5433/pis_test",
                        github_webhook_secret=SECRET)
    client = TestClient(create_app(settings))
    body = json.dumps(PAYLOAD).encode()

    r = client.post("/v1/github/webhook", content=body,
                    headers={"X-Hub-Signature-256": "sha256=" + "0" * 64,
                             "X-GitHub-Event": "push", "X-GitHub-Delivery": "d-9"})
    assert r.status_code == 401

    r = client.post("/v1/github/webhook", content=body,
                    headers={"X-Hub-Signature-256": sign(body),
                             "X-GitHub-Event": "push", "X-GitHub-Delivery": "d-9",
                             "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["status"] == "processed"
