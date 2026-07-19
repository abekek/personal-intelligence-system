"""Repair passes over existing memories (post-QA):
evidence dedup, retraction of note-only memories, near-duplicate merge,
LLM-verdict supersession for same-topic conflicts."""
from __future__ import annotations

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from pis.extraction.extractor import CONFLICT_SIMILARITY, MERGE_SIMILARITY, classify_pairs


def dedup_evidence(db: Session) -> int:
    result = db.execute(sa_text("""
        DELETE FROM memory_evidence a USING memory_evidence b
        WHERE a.memory_id = b.memory_id AND a.event_id = b.event_id AND a.id > b.id
    """))
    db.commit()
    return result.rowcount


def retract_note_only(db: Session) -> int:
    """Retract memories whose every evidence event is a kb_capture_note."""
    result = db.execute(sa_text("""
        UPDATE memory_items SET status = 'retracted', authority = 'asserted'
        WHERE status = 'current' AND memory_id IN (
            SELECT me.memory_id FROM memory_evidence me
            JOIN events e ON e.event_id = me.event_id
            GROUP BY me.memory_id
            HAVING count(*) FILTER (WHERE e.capture_method != 'manual') = 0
        )
    """))
    db.commit()
    return result.rowcount


def _neighbors(db: Session, memory_id: str, low: float, high: float) -> list[tuple]:
    return list(db.execute(sa_text("""
        SELECT o.memory_id, o.statement,
               1 - (o.embedding <=> s.embedding) AS sim
        FROM memory_items s, memory_items o
        WHERE s.memory_id = :m AND o.memory_id != :m
          AND o.status = 'current' AND o.embedding IS NOT NULL
          AND s.embedding IS NOT NULL
          AND o.last_confirmed_at <= s.last_confirmed_at
          AND 1 - (o.embedding <=> s.embedding) >= :low
          AND 1 - (o.embedding <=> s.embedding) < :high
        ORDER BY sim DESC LIMIT 4
    """), {"m": memory_id, "low": low, "high": high}))


def _batch(db: Session, after: str, limit: int) -> list[tuple[str, str]]:
    return list(db.execute(sa_text("""
        SELECT memory_id, statement FROM memory_items
        WHERE status = 'current' AND memory_id > :after
        ORDER BY memory_id LIMIT :limit
    """), {"after": after, "limit": limit}))


def merge_batch(db: Session, after: str = "", limit: int = 300) -> dict:
    """Absorb near-duplicates (sim >= MERGE) into the surviving memory:
    union evidence, mark absorbed superseded."""
    merged = 0
    rows = _batch(db, after, limit)
    for memory_id, _ in rows:
        for other_id, _, _ in _neighbors(db, memory_id, MERGE_SIMILARITY, 1.001):
            db.execute(sa_text("""
                INSERT INTO memory_evidence (id, memory_id, event_id, excerpt)
                SELECT 'evi_' || substr(md5(random()::text), 1, 16),
                       CAST(:survivor AS varchar), e.event_id, e.excerpt
                FROM memory_evidence e WHERE e.memory_id = :absorbed
                  AND e.event_id NOT IN (
                    SELECT event_id FROM memory_evidence WHERE memory_id = :survivor)
            """), {"survivor": memory_id, "absorbed": other_id})
            db.execute(sa_text(
                "UPDATE memory_items SET status = 'superseded' WHERE memory_id = :m"),
                {"m": other_id})
            db.execute(sa_text(
                "UPDATE memory_items SET authority = CASE WHEN authority = 'observed' "
                "THEN 'corroborated' ELSE authority END WHERE memory_id = :m"),
                {"m": memory_id})
            merged += 1
    db.commit()
    next_after = rows[-1][0] if len(rows) == limit else ""
    return {"scanned": len(rows), "merged": merged, "next_after": next_after}


def supersede_batch(db: Session, llm, after: str = "", limit: int = 100) -> dict:
    """Same-topic conflicts (CONFLICT <= sim < MERGE): newer statement wins
    when the LLM says it supersedes the older one."""
    superseded = 0
    rows = _batch(db, after, limit)
    for memory_id, statement in rows:
        current = db.execute(sa_text(
            "SELECT status FROM memory_items WHERE memory_id = :m"),
            {"m": memory_id}).scalar()
        if current != "current":
            continue
        neighbors = _neighbors(db, memory_id, CONFLICT_SIMILARITY, MERGE_SIMILARITY)
        if not neighbors:
            continue
        pairs = [(old_statement, statement) for _, old_statement, _ in neighbors]
        relations = classify_pairs(llm, pairs)
        for (other_id, _, _), relation in zip(neighbors, relations):
            if relation == "supersedes":
                db.execute(sa_text(
                    "UPDATE memory_items SET status = 'superseded' "
                    "WHERE memory_id = :m AND status = 'current'"), {"m": other_id})
                db.execute(sa_text(
                    "UPDATE memory_items SET supersedes_memory_id = "
                    "coalesce(supersedes_memory_id, :o) WHERE memory_id = :m"),
                    {"o": other_id, "m": memory_id})
                superseded += 1
    db.commit()
    next_after = rows[-1][0] if len(rows) == limit else ""
    return {"scanned": len(rows), "superseded": superseded, "next_after": next_after}
