"""Phase 6 (lightweight): human-in-the-loop memory curation.

User verdicts outrank machine extraction: confirmed memories get
`user_confirmed` authority (the top tier), corrections supersede the old
statement and are themselves ledger-evidenced, retractions hide without
deleting. Nothing here ever deletes a row.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from pis.config import Settings
from pis.schemas.events import CanonicalEvent, ContentPart, EventType


def _get(db: Session, memory_id: str):
    return db.execute(sa_text(
        "SELECT memory_id, kind, statement, status FROM memory_items "
        "WHERE memory_id = :m"), {"m": memory_id}).first()


def confirm_memory(db: Session, memory_id: str) -> dict:
    row = _get(db, memory_id)
    if row is None:
        return {"error": "memory not found"}
    now = datetime.now(timezone.utc)
    db.execute(sa_text(
        "UPDATE memory_items SET authority = 'user_confirmed', "
        "last_confirmed_at = :now, status = 'current' WHERE memory_id = :m"),
        {"now": now, "m": memory_id})
    db.commit()
    return {"memory_id": memory_id, "authority": "user_confirmed",
            "statement": row[2]}


def retract_memory(db: Session, memory_id: str, reason: str = "") -> dict:
    row = _get(db, memory_id)
    if row is None:
        return {"error": "memory not found"}
    db.execute(sa_text(
        "UPDATE memory_items SET status = 'retracted' WHERE memory_id = :m"),
        {"m": memory_id})
    from pis.ingest.service import audit
    audit(db, "memory.retracted", memory_id, reason=reason[:200])
    db.commit()
    return {"memory_id": memory_id, "status": "retracted"}


def correct_memory(db: Session, settings: Settings, policy, memory_id: str,
                   corrected_statement: str, note: str = "",
                   embedder=None) -> dict:
    """Supersede a memory with a user-stated correction. The correction is
    written to the ledger as an event (provenance for the new memory)."""
    from pis.ingest.service import ingest_events

    row = _get(db, memory_id)
    if row is None:
        return {"error": "memory not found"}
    old_id, kind, old_statement, _ = row

    correction_event = CanonicalEvent(
        event_type=EventType.MESSAGE_COMPLETED,
        provider="claude",
        provider_conversation_id="claude-ai/corrections",
        role="user",
        occurred_at=datetime.now(timezone.utc),
        capture_method="manual",
        content_parts=[ContentPart(
            type="text",
            text=f"Correction of memory {old_id} ('{old_statement[:120]}'): "
                 f"{corrected_statement}" + (f" — {note}" if note else ""))],
        metadata={"source": "memory_correction", "corrects": old_id},
    )
    [ingest_result] = ingest_events(db, [correction_event], policy)

    now = datetime.now(timezone.utc)
    new_id = "mem_" + uuid.uuid4().hex[:16]
    vec_literal = None
    if embedder is not None:
        try:
            from pis.embeddings import to_pgvector
            vec_literal = to_pgvector(embedder([corrected_statement])[0])
        except Exception:
            vec_literal = None
    db.execute(sa_text("""
        INSERT INTO memory_items (memory_id, kind, statement, status, authority,
            confidence, first_observed_at, last_confirmed_at, sensitivity,
            supersedes_memory_id, source_conversation_id, embedding)
        SELECT CAST(:new_id AS varchar), kind, CAST(:stmt AS text),
               'current', 'user_confirmed', 1.0,
               CAST(:now AS timestamptz), CAST(:now AS timestamptz),
               sensitivity, CAST(:old_id AS varchar), source_conversation_id,
               CAST(:vec AS vector)
        FROM memory_items WHERE memory_id = :old_id
    """), {"new_id": new_id, "stmt": corrected_statement, "now": now,
           "old_id": old_id, "vec": vec_literal})
    db.execute(sa_text(
        "UPDATE memory_items SET status = 'superseded' WHERE memory_id = :m"),
        {"m": old_id})
    if ingest_result.status == "created":
        db.execute(sa_text(
            "INSERT INTO memory_evidence (id, memory_id, event_id, excerpt) "
            "VALUES (:i, :m, :e, :x)"),
            {"i": "evi_" + uuid.uuid4().hex[:16], "m": new_id,
             "e": correction_event.event_id,
             "x": corrected_statement[:300]})
    # inherit the old memory's evidence trail for context
    db.execute(sa_text("""
        INSERT INTO memory_evidence (id, memory_id, event_id, excerpt)
        SELECT 'evi_' || substr(md5(random()::text), 1, 16),
               CAST(:new_id AS varchar), e.event_id, e.excerpt
        FROM memory_evidence e WHERE e.memory_id = :old_id
          AND e.event_id NOT IN (
            SELECT event_id FROM memory_evidence WHERE memory_id = :new_id)
    """), {"new_id": new_id, "old_id": old_id})
    db.commit()
    return {"memory_id": new_id, "supersedes": old_id,
            "authority": "user_confirmed", "statement": corrected_statement}
