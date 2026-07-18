from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    CONVERSATION_CREATED = "conversation.created"
    CONVERSATION_TITLE_CHANGED = "conversation.title.changed"
    MESSAGE_STARTED = "conversation.message.started"
    MESSAGE_COMPLETED = "conversation.message.completed"
    MESSAGE_REVISED = "conversation.message.revised"
    ATTACHMENT_ADDED = "conversation.attachment.added"
    ARTIFACT_CREATED = "conversation.artifact.created"
    CC_SESSION_STARTED = "claude_code.session.started"
    CC_TOOL_COMPLETED = "claude_code.tool.completed"
    CC_FILE_CHANGED = "claude_code.file.changed"
    CC_TURN_COMPLETED = "claude_code.turn.completed"
    CC_SESSION_ENDED = "claude_code.session.ended"
    GITHUB_PUSH = "github.push"
    GITHUB_PR_UPDATED = "github.pull_request.updated"
    GITHUB_ISSUE_UPDATED = "github.issue.updated"


Provider = Literal["chatgpt", "claude", "claude_code", "github", "system"]
Sensitivity = Literal["public", "internal", "confidential-personal", "highly-sensitive"]
CaptureMethod = Literal["claude_code_hook", "browser_extension", "export_import", "webhook", "manual"]
Role = Literal["user", "assistant", "system", "tool"]


def new_event_id() -> str:
    return "evt_" + uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "code", "table", "citation", "artifact_ref", "attachment"]
    text: str = ""
    language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    confidence: float


class CanonicalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=new_event_id)
    event_type: EventType
    provider: Provider
    provider_account_id: str | None = None
    provider_conversation_id: str | None = None
    provider_message_id: str | None = None
    provider_parent_message_id: str | None = None
    revision: int = 1
    role: Role | None = None
    occurred_at: datetime
    captured_at: datetime = Field(default_factory=utcnow)
    capture_method: CaptureMethod
    project_candidates: list[ProjectCandidate] = Field(default_factory=list)
    content_parts: list[ContentPart] = Field(default_factory=list)
    source_uri: str | None = None
    content_hash: str = ""
    sensitivity: Sensitivity = "confidential-personal"
    capture_device_id: str | None = None
    raw_object_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.content_hash:
            self.content_hash = compute_content_hash(self)


def compute_content_hash(event: CanonicalEvent) -> str:
    material = {
        "event_type": str(event.event_type),
        "provider": event.provider,
        "provider_conversation_id": event.provider_conversation_id,
        "provider_message_id": event.provider_message_id,
        "revision": event.revision,
        "role": event.role,
        "occurred_at": event.occurred_at.isoformat(),
        "content_parts": [p.model_dump() for p in event.content_parts],
        "metadata": event.metadata,
        "source_uri": event.source_uri,
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"sha256:{digest}"
