from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pis.db.models import CodeSession, GitObject, Repository, SessionCommitLink, Turn


def link_commit_to_sessions(db: Session, git_object_id: str) -> list[SessionCommitLink]:
    commit = db.get(GitObject, git_object_id)
    repo = db.get(Repository, commit.repository_id)
    commit_files = set(commit.files or [])
    links: list[SessionCommitLink] = []

    for session in db.scalars(select(CodeSession)).all():
        if not session.git_remote or repo.full_name not in session.git_remote:
            continue
        head_match = session.head_commit == commit.object_key
        branch_match = bool(session.branch and session.branch == commit.ref)
        session_files: set[str] = set()
        for turn in db.scalars(select(Turn).where(Turn.session_id == session.session_id)):
            session_files.update(turn.changed_files or [])
        overlap = (
            len(session_files & commit_files) / len(commit_files) if commit_files else 0.0
        )
        confidence = 1.0 if head_match else 0.5 * branch_match + 0.5 * min(1.0, overlap)
        if confidence < 0.5:
            continue
        existing = db.scalar(select(SessionCommitLink).where(
            SessionCommitLink.session_id == session.session_id,
            SessionCommitLink.git_object_id == git_object_id,
        ))
        if existing is not None:
            continue
        link = SessionCommitLink(
            session_id=session.session_id, git_object_id=git_object_id,
            confidence=round(confidence, 3),
            signals={"branch_match": branch_match, "file_overlap": round(overlap, 3),
                     "head_commit_match": head_match},
        )
        db.add(link)
        links.append(link)
    db.commit()
    return links
