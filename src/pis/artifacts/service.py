"""Artifact ingestion: content-addressed storage + chunk indexing."""
from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pis import ids
from pis.artifacts.extract import chunk_blocks, extract_text
from pis.db.models import Artifact, ArtifactChunk, ArtifactVersion
from pis.security.secrets import redact_text


def link_artifact_to_conversation(
    db: Session, artifact_id: str, conversation_id: str,
    display_name: str | None, provider_reference: dict | None = None,
) -> None:
    """Idempotently record that a conversation references an artifact."""
    from pis.db.models import ArtifactReference
    ref_id = "ref_" + ids._h(f"{artifact_id}:{conversation_id}")
    if db.get(ArtifactReference, ref_id) is None:
        db.add(ArtifactReference(
            id=ref_id, artifact_id=artifact_id, conversation_id=conversation_id,
            display_name=display_name, resolution_status="resolved_exact",
            provider_reference=provider_reference or {},
        ))
        db.commit()


@dataclass
class ArtifactResult:
    status: str  # "created" | "duplicate" | "unsupported"
    artifact_id: str | None = None
    version_id: str | None = None
    chunks: int = 0


def ingest_file(db: Session, store, data: bytes, filename: str,
                source_meta: dict | None = None, embedder=None) -> ArtifactResult:
    sha = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(ArtifactVersion).where(ArtifactVersion.sha256 == sha))
    if existing is not None:
        return ArtifactResult("duplicate", existing.artifact_id, existing.id)

    extraction = extract_text(data, filename)
    object_id = store.put(data)

    artifact_id = "art_" + ids._h(f"{filename}:{sha}")
    version_id = f"artv_{sha[:24]}"
    db.add(Artifact(artifact_id=artifact_id, artifact_kind="document",
                    original_filename=filename, title=filename))
    db.flush()
    db.add(ArtifactVersion(
        id=version_id, artifact_id=artifact_id, sha256=sha, byte_size=len(data),
        mime_type=mimetypes.guess_type(filename)[0], object_id=object_id,
        text_extracted=bool(extraction and extraction.blocks),
        parser=extraction.parser if extraction else None,
        source_meta=source_meta or {},
    ))
    db.flush()

    if extraction is None:
        db.commit()
        return ArtifactResult("unsupported", artifact_id, version_id)

    chunks = chunk_blocks(extraction.blocks)
    for ordinal, chunk in enumerate(chunks):
        db.add(ArtifactChunk(
            id=f"{version_id}:{ordinal}", version_id=version_id, ordinal=ordinal,
            text_content=redact_text(chunk.text), locator=chunk.locator,
        ))
    db.flush()
    if embedder is not None and chunks:
        from pis.embeddings import to_pgvector
        from sqlalchemy import text as sa_text
        try:
            vectors = embedder([c.text for c in chunks])
            for ordinal, vec in enumerate(vectors):
                db.execute(sa_text(
                    "UPDATE artifact_chunks SET embedding = CAST(:v AS vector) "
                    "WHERE id = :i"), {"v": to_pgvector(vec), "i": f"{version_id}:{ordinal}"})
        except Exception:
            pass  # embeddings are best-effort
    db.commit()
    return ArtifactResult("created", artifact_id, version_id, len(chunks))
