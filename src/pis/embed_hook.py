"""Ingest-time embedding: after an event is normalized, embed the projected
revision/turn rows. Best-effort — failures are audited, never raised."""
from __future__ import annotations

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from pis.config import Settings
from pis.embeddings import embed_texts, to_pgvector
from pis.schemas.events import CanonicalEvent


def embed_event_rows(db: Session, ev: CanonicalEvent, settings: Settings,
                     embedder=embed_texts) -> int:
    """Embed rows produced by this event. Returns rows embedded."""
    rows = list(db.execute(sa_text("""
        SELECT 'revision' AS kind, id, coalesce(text_content, '') AS content
        FROM message_revisions WHERE event_id = :eid AND embedding IS NULL
        UNION ALL
        SELECT 'turn', id, coalesce(user_prompt, '') || ' ' || coalesce(assistant_response, '')
        FROM turns WHERE event_id = :eid AND embedding IS NULL
    """), {"eid": ev.event_id}))
    todo = [(kind, rid, content) for kind, rid, content in rows if content.strip()]
    if not todo:
        return 0
    vectors = embedder([content for _, _, content in todo], settings)
    for (kind, rid, _), vec in zip(todo, vectors):
        table = "message_revisions" if kind == "revision" else "turns"
        db.execute(sa_text(
            f"UPDATE {table} SET embedding = CAST(:vec AS vector) WHERE id = :rid"
        ), {"vec": to_pgvector(vec), "rid": rid})
    return len(todo)


def make_embed_hook(settings: Settings, embedder=embed_texts):
    def hook(db: Session, ev: CanonicalEvent) -> None:
        from pis.ingest.service import audit
        try:
            embed_event_rows(db, ev, settings, embedder)
        except Exception as exc:  # never block ingestion on embedding failures
            audit(db, "embed.failed", ev.event_id, error=str(exc)[:200])
    return hook
