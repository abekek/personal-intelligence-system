from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from pis import ids
from pis.db.models import GitObject, Repository, WebhookDelivery
from pis.ingest.service import audit, ingest_events
from pis.linking.sessions import link_commit_to_sessions
from pis.policy.engine import PolicyEngine
from pis.schemas.events import CanonicalEvent, EventType


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@dataclass
class HandleResult:
    status: str  # "processed" | "duplicate" | "rejected" | "ignored"
    created_commits: list[str] = field(default_factory=list)


def handle_delivery(
    db: Session, policy: PolicyEngine, delivery_id: str, event_name: str, payload: dict
) -> HandleResult:
    if db.get(WebhookDelivery, delivery_id) is not None:
        return HandleResult("duplicate")
    db.add(WebhookDelivery(delivery_id=delivery_id, event_name=event_name))

    if event_name != "push":
        db.commit()
        return HandleResult("ignored")

    full_name = payload["repository"]["full_name"]
    if policy.is_denied_repo(f"github.com/{full_name}"):
        audit(db, "webhook.rejected", delivery_id, reason="denied_repository",
              repository=full_name)
        db.commit()
        return HandleResult("rejected")

    branch = payload.get("ref", "").removeprefix("refs/heads/")
    commits = payload.get("commits", [])
    head_ts = (payload.get("head_commit") or {}).get("timestamp")
    occurred_at = datetime.fromisoformat(head_ts) if head_ts else datetime.now().astimezone()

    ledger_event = CanonicalEvent(
        event_type=EventType.GITHUB_PUSH, provider="github",
        occurred_at=occurred_at, capture_method="webhook",
        source_uri=payload.get("compare"),
        metadata={
            "delivery_id": delivery_id, "repository_full_name": full_name,
            "ref": payload.get("ref"), "commit_ids": [c["id"] for c in commits],
        },
    )
    [ingest_result] = ingest_events(db, [ledger_event], policy)
    if ingest_result.status == "rejected":
        return HandleResult("rejected")

    rid = ids.repo_id(full_name)
    if db.get(Repository, rid) is None:
        db.add(Repository(id=rid, full_name=full_name,
                          default_branch=payload["repository"].get("default_branch")))
        db.flush()

    created: list[str] = []
    for commit in commits:
        gid = ids.git_object_id(rid, "commit", commit["id"])
        if db.get(GitObject, gid) is not None:
            continue
        files = sorted(set(commit.get("added", []) + commit.get("modified", [])
                           + commit.get("removed", [])))
        db.add(GitObject(
            id=gid, repository_id=rid, object_type="commit", object_key=commit["id"],
            title=commit.get("message"), author=(commit.get("author") or {}).get("name"),
            ref=branch, files=files,
            occurred_at=datetime.fromisoformat(commit["timestamp"]),
            payload=commit, event_id=ledger_event.event_id,
        ))
        created.append(gid)
    db.commit()
    for gid in created:
        link_commit_to_sessions(db, gid)
    return HandleResult("processed", created)
