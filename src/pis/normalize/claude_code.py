from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pis import ids
from pis.db.models import CodeSession, Conversation, Message, MessageRevision, ToolEvent, Turn
from pis.normalize.registry import register
from pis.schemas.events import CanonicalEvent


def _role_text(ev: CanonicalEvent, role: str) -> str:
    return "\n".join(
        p.text for p in ev.content_parts if p.metadata.get("role") == role and p.text
    )


def normalize_turn_completed(db: Session, ev: CanonicalEvent) -> None:
    meta = ev.metadata
    session_id = str(meta.get("session_id") or ev.provider_conversation_id)

    session = db.get(CodeSession, session_id)
    if session is None:
        session = CodeSession(session_id=session_id, started_at=ev.occurred_at)
        db.add(session)
    session.cwd = meta.get("cwd") or session.cwd
    session.repo_root = meta.get("repo_root") or session.repo_root
    session.git_remote = meta.get("git_remote") or session.git_remote
    session.branch = meta.get("branch") or session.branch
    session.head_commit = meta.get("head_commit") or session.head_commit
    session.last_seen_at = ev.occurred_at

    conv_id = ids.conversation_id("claude_code", session_id)
    user_prompt = _role_text(ev, "user")
    assistant_text = _role_text(ev, "assistant")

    conv = db.get(Conversation, conv_id)
    if conv is None:
        conv = Conversation(
            id=conv_id, provider="claude_code", provider_conversation_id=session_id,
            title=user_prompt[:80] or None, started_at=ev.occurred_at,
            updated_at=ev.occurred_at, sensitivity=ev.sensitivity,
        )
        db.add(conv)
    conv.updated_at = ev.occurred_at

    n = db.scalar(select(func.count()).select_from(Turn).where(Turn.session_id == session_id)) or 0
    turn = Turn(
        id=ids.turn_id(session_id, n), session_id=session_id, conversation_id=conv_id,
        user_prompt=user_prompt, assistant_response=assistant_text,
        changed_files=list(meta.get("changed_files") or []),
        occurred_at=ev.occurred_at, event_id=ev.event_id,
    )
    db.add(turn)
    # Explicit flush per FK level: without relationship() constructs the
    # unit-of-work does not order inserts across mappers by foreign key.
    db.flush()

    for offset, (role, text_) in enumerate([("user", user_prompt), ("assistant", assistant_text)]):
        mid = ids.message_id(conv_id, f"{turn.id}:{role}")
        db.add(Message(
            id=mid, conversation_id=conv_id, role=role,
            position=2 * n + offset, created_at=ev.occurred_at,
        ))
        db.flush()
        db.add(MessageRevision(
            id=f"{mid}:1", message_id=mid, revision=1, text_content=text_,
            content_parts={"parts": [{"type": "text", "text": text_}]},
            event_id=ev.event_id, created_at=ev.occurred_at,
        ))

    for i, tool in enumerate(meta.get("tool_uses") or []):
        db.add(ToolEvent(
            id=ids.tool_event_id(turn.id, i), conversation_id=conv_id, turn_id=turn.id,
            tool_name=str(tool.get("tool_name", "unknown")),
            summary=dict(tool.get("summary") or {}),
            occurred_at=ev.occurred_at, event_id=ev.event_id,
        ))
    db.flush()


register("claude_code.turn.completed", normalize_turn_completed)
