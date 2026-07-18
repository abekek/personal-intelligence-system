import json
from pathlib import Path

import sqlalchemy as sa

from pis.db.models import SessionCommitLink
from pis.github.webhook import handle_delivery
from pis.ingest.service import ingest_events
from pis.linking.sessions import link_commit_to_sessions
from pis.policy.engine import PolicyEngine
from tests.test_normalize_claude_code import turn_event

PAYLOAD = json.loads(Path("tests/fixtures/github_push.json").read_text())


def policy():
    return PolicyEngine.load(Path("config"))


def test_commit_links_to_producing_session(db):
    # session sess-abc: remote abekek/demo, branch main, changed src/utils.py,
    # head_commit "a"*40 == fixture commit sha
    ingest_events(db, [turn_event()], policy())
    result = handle_delivery(db, policy(), "delivery-link-1", "push", PAYLOAD)
    assert result.status == "processed"

    [link] = db.scalars(sa.select(SessionCommitLink)).all()
    assert link.session_id == "sess-abc"
    assert link.confidence == 1.0
    assert link.signals["head_commit_match"] is True


def test_no_link_for_unrelated_repo(db):
    ingest_events(db, [turn_event()], policy())
    other = dict(PAYLOAD, repository={"full_name": "abekek/other-repo"})
    handle_delivery(db, policy(), "delivery-link-2", "push", other)
    assert db.scalars(sa.select(SessionCommitLink)).all() == []


def test_linking_is_idempotent(db):
    ingest_events(db, [turn_event()], policy())
    result = handle_delivery(db, policy(), "delivery-link-3", "push", PAYLOAD)
    link_commit_to_sessions(db, result.created_commits[0])  # second call, no dupe
    assert db.scalar(sa.select(sa.func.count()).select_from(SessionCommitLink)) == 1
