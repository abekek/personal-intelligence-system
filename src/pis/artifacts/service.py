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


def resolve_references(db: Session, provider: str,
                       references: list[dict]) -> dict:
    """Match export binary references to stored artifacts by filename.

    The claude.ai export carries file names but never bytes; the artifact
    store holds documents ingested from disk scans. Matches become
    resolved_by_name references; misses are persisted unresolved so the
    coverage gap stays queryable. Idempotent per (conversation, name);
    re-running upgrades previously-unresolved rows when an artifact has
    appeared since."""
    from sqlalchemy import text as sa_text

    from pis.db.models import ArtifactReference

    counts = {"resolved": 0, "unresolved": 0, "already_resolved": 0, "upgraded": 0}
    for ref in references:
        name = (ref.get("file_name") or "").strip()
        conversation_uuid = ref.get("conversation_uuid") or ""
        if not name or not conversation_uuid:
            continue
        conversation_id = ids.conversation_id(provider, conversation_uuid)
        ref_id = "ref_" + ids._h(f"byname:{conversation_id}:{name}")
        artifact_id = db.execute(sa_text("""
            SELECT a.artifact_id FROM artifacts a
            JOIN artifact_versions v ON v.artifact_id = a.artifact_id
            WHERE a.original_filename = :name
            ORDER BY v.created_at DESC LIMIT 1
        """), {"name": name}).scalar()
        existing = db.get(ArtifactReference, ref_id)
        if existing is not None:
            if existing.artifact_id is None and artifact_id is not None:
                existing.artifact_id = artifact_id
                existing.resolution_status = "resolved_by_name"
                counts["upgraded"] += 1
            else:
                counts["already_resolved"] += 1
            continue
        db.add(ArtifactReference(
            id=ref_id, artifact_id=artifact_id, conversation_id=conversation_id,
            display_name=name,
            resolution_status=("resolved_by_name" if artifact_id
                               else "unresolved_missing_binary"),
            provider_reference={"provider": provider,
                                "conversation_uuid": conversation_uuid},
        ))
        counts["resolved" if artifact_id else "unresolved"] += 1
    db.commit()
    return counts


@dataclass
class ArtifactResult:
    status: str  # "created" | "duplicate" | "unsupported"
    artifact_id: str | None = None
    version_id: str | None = None
    chunks: int = 0


def ingest_file(db: Session, store, data: bytes, filename: str,
                source_meta: dict | None = None, embedder=None) -> ArtifactResult:
    from sqlalchemy.exc import IntegrityError

    from pis.security.filenames import is_denied_filename
    if is_denied_filename(filename):
        return ArtifactResult("denied")

    sha = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(ArtifactVersion).where(ArtifactVersion.sha256 == sha))
    if existing is not None:
        return ArtifactResult("duplicate", existing.artifact_id, existing.id)
    try:
        return _ingest_new(db, store, data, filename, sha, source_meta, embedder)
    except IntegrityError:
        # Client-retry race: a previous attempt finished server-side after
        # the client timed out. The sha unique constraint makes this safe.
        db.rollback()
        existing = db.scalar(select(ArtifactVersion).where(ArtifactVersion.sha256 == sha))
        if existing is not None:
            return ArtifactResult("duplicate", existing.artifact_id, existing.id)
        raise


def _ingest_new(db: Session, store, data: bytes, filename: str, sha: str,
                source_meta: dict | None, embedder) -> ArtifactResult:

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
