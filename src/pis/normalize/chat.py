"""Normalizer for chat messages (claude.ai capture notes, future export imports)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pis import ids
from pis.db.models import Conversation, Message, MessageRevision
from pis.normalize.registry import register
from pis.schemas.events import CanonicalEvent


def normalize_message_completed(db: Session, ev: CanonicalEvent) -> None:
    provider_conversation_id = ev.provider_conversation_id or "unknown-conversation"
    conv_id = ids.conversation_id(ev.provider, provider_conversation_id)
    text = "\n".join(p.text for p in ev.content_parts if p.text)

    title = ev.metadata.get("conversation_title") or text[:80] or None
    conv = db.get(Conversation, conv_id)
    if conv is None:
        conv = Conversation(
            id=conv_id, provider=ev.provider,
            provider_conversation_id=provider_conversation_id,
            title=title, started_at=ev.occurred_at,
            updated_at=ev.occurred_at, source_uri=ev.source_uri,
            sensitivity=ev.sensitivity,
        )
        db.add(conv)
        db.flush()
    conv.updated_at = ev.occurred_at

    position = db.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conv_id)
    ) or 0
    message_key = ev.provider_message_id or f"{ev.event_id}"
    mid = ids.message_id(conv_id, message_key)
    db.add(Message(
        id=mid, conversation_id=conv_id, provider_message_id=ev.provider_message_id,
        role=ev.role or "assistant", position=position, created_at=ev.occurred_at,
    ))
    db.flush()
    db.add(MessageRevision(
        id=f"{mid}:{ev.revision}", message_id=mid, revision=ev.revision,
        text_content=text,
        content_parts={"parts": [p.model_dump() for p in ev.content_parts]},
        event_id=ev.event_id, created_at=ev.occurred_at,
    ))
    db.flush()


register("conversation.message.completed", normalize_message_completed)
