from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pis.db.models import AuditLog, Event
from pis.normalize.registry import get_normalizer
from pis.policy.engine import PolicyEngine
from pis.schemas.events import CanonicalEvent
from pis.security.secrets import scan_text


@dataclass
class IngestResult:
    event_id: str
    status: str  # "created" | "duplicate" | "rejected"
    reason: str | None = None


def audit(db: Session, action: str, target: str | None = None, **details) -> None:
    db.add(AuditLog(actor="ingest", action=action, target=target, details=details))


def ingest_events(
    db: Session, events: list[CanonicalEvent], policy: PolicyEngine,
    embed_hook=None,
) -> list[IngestResult]:
    results: list[IngestResult] = []
    for ev in events:
        reason = policy.check_event(ev)
        if reason is None:
            joined = "\n".join(p.text for p in ev.content_parts if p.text)
            matches = scan_text(joined)
            if matches:
                reason = f"secret_detected:{matches[0].kind}"
        if reason is not None:
            audit(db, "ingest.rejected", ev.event_id,
                  reason=reason, event_type=str(ev.event_type))
            results.append(IngestResult(ev.event_id, "rejected", reason))
            continue

        duplicate = db.scalar(
            select(Event.event_id).where(
                (Event.event_id == ev.event_id) | (Event.content_hash == ev.content_hash)
            )
        )
        if duplicate is not None:
            results.append(IngestResult(ev.event_id, "duplicate"))
            continue

        db.add(Event(
            event_id=ev.event_id, event_type=str(ev.event_type), provider=ev.provider,
            provider_conversation_id=ev.provider_conversation_id,
            provider_message_id=ev.provider_message_id, revision=ev.revision,
            role=ev.role, occurred_at=ev.occurred_at, captured_at=ev.captured_at,
            capture_method=ev.capture_method, content_hash=ev.content_hash,
            sensitivity=ev.sensitivity, raw_object_id=ev.raw_object_id,
            payload=ev.model_dump(mode="json"),
        ))
        db.flush()
        audit(db, "ingest.created", ev.event_id, event_type=str(ev.event_type))
        normalizer = get_normalizer(str(ev.event_type))
        if normalizer is not None:
            normalizer(db, ev)
        if embed_hook is not None:
            embed_hook(db, ev)
        results.append(IngestResult(ev.event_id, "created"))
    db.commit()
    return results
