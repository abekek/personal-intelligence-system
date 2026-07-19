"""Shared extraction driver: used by the admin endpoint and the in-service
scheduler ticker."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text as sa_text

from pis.config import Settings
from pis.extraction.extractor import PROMPT_VERSION, bedrock_llm, extract_conversation


def run_extraction(session_factory, settings: Settings, limit: int = 5,
                   llm=None, embedder=None) -> dict:
    with session_factory() as db:
        conversation_ids = [cid for (cid,) in db.execute(sa_text("""
            SELECT id FROM conversations
            WHERE extracted_at IS NULL OR extracted_at < updated_at
            ORDER BY updated_at DESC LIMIT :limit"""), {"limit": limit})]
        if not conversation_ids:
            return {"processed": 0, "remaining": 0}
        run_id = "run_" + uuid.uuid4().hex[:16]
        db.execute(sa_text(
            "INSERT INTO extraction_runs (id, model, prompt_version, stats) "
            "VALUES (:i, :m, :p, '{}')"),
            {"i": run_id, "m": settings.extraction_model, "p": PROMPT_VERSION})
        db.commit()

        if llm is None:
            llm = bedrock_llm(settings)
        if embedder is None:
            from pis.embeddings import embed_texts
            embedder = lambda texts: embed_texts(texts, settings)  # noqa: E731

        totals = {"proposed": 0, "created": 0, "confirmed": 0,
                  "superseded": 0, "errors": 0}
        for conversation_id in conversation_ids:
            try:
                counts = extract_conversation(db, conversation_id, llm, embedder, run_id)
                for key in ("proposed", "created", "confirmed", "superseded"):
                    totals[key] += counts.get(key, 0)
            except Exception:
                db.rollback()
                totals["errors"] += 1
        remaining = db.execute(sa_text(
            "SELECT count(*) FROM conversations "
            "WHERE extracted_at IS NULL OR extracted_at < updated_at")).scalar()
        db.execute(sa_text(
            "UPDATE extraction_runs SET completed_at = :n, stats = :s WHERE id = :i"),
            {"n": datetime.now(timezone.utc), "s": json.dumps(totals), "i": run_id})
        db.commit()
        return {"processed": len(conversation_ids), "remaining": remaining, **totals}
