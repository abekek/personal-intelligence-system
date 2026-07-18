from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        dict: JSONB,
        datetime: DateTime(timezone=True),
        str: String,
    }


class Event(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(index=True)
    provider: Mapped[str] = mapped_column(index=True)
    provider_conversation_id: Mapped[str | None]
    provider_message_id: Mapped[str | None]
    revision: Mapped[int] = mapped_column(Integer, default=1)
    role: Mapped[str | None]
    occurred_at: Mapped[datetime]
    captured_at: Mapped[datetime]
    capture_method: Mapped[str]
    content_hash: Mapped[str] = mapped_column(unique=True)
    sensitivity: Mapped[str] = mapped_column(default="confidential-personal")
    raw_object_id: Mapped[str | None]
    payload: Mapped[dict]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("provider", "provider_conversation_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    provider: Mapped[str]
    provider_conversation_id: Mapped[str]
    title: Mapped[str | None]
    started_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    source_uri: Mapped[str | None]
    primary_project_id: Mapped[str | None]
    capture_status: Mapped[str] = mapped_column(default="live")
    sensitivity: Mapped[str] = mapped_column(default="confidential-personal")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "position"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    provider_message_id: Mapped[str | None]
    role: Mapped[str]
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]


class MessageRevision(Base):
    __tablename__ = "message_revisions"
    __table_args__ = (
        UniqueConstraint("message_id", "revision"),
        Index("ix_message_revisions_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    text_content: Mapped[str | None] = mapped_column(Text)
    content_parts: Mapped[dict] = mapped_column(default=dict)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"))
    created_at: Mapped[datetime]
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(text_content, ''))", persisted=True),
        nullable=True,
    )


class CodeSession(Base):
    __tablename__ = "code_sessions"

    session_id: Mapped[str] = mapped_column(primary_key=True)
    cwd: Mapped[str | None]
    repo_root: Mapped[str | None]
    git_remote: Mapped[str | None]
    branch: Mapped[str | None]
    head_commit: Mapped[str | None]
    project_id: Mapped[str | None]
    started_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_tsv", "tsv", postgresql_using="gin"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("code_sessions.session_id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_prompt: Mapped[str | None] = mapped_column(Text)
    assistant_response: Mapped[str | None] = mapped_column(Text)
    changed_files: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    occurred_at: Mapped[datetime]
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"))
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(user_prompt, '') || ' ' || coalesce(assistant_response, ''))",
            persisted=True,
        ),
        nullable=True,
    )


class ToolEvent(Base):
    __tablename__ = "tool_events"

    id: Mapped[str] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("turns.id"))
    tool_name: Mapped[str]
    summary: Mapped[dict] = mapped_column(default=dict)
    occurred_at: Mapped[datetime]
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"))


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(default="github")
    full_name: Mapped[str] = mapped_column(unique=True)
    default_branch: Mapped[str | None]
    project_id: Mapped[str | None]


class GitObject(Base):
    __tablename__ = "git_objects"
    __table_args__ = (UniqueConstraint("repository_id", "object_type", "object_key"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), index=True)
    object_type: Mapped[str]
    object_key: Mapped[str] = mapped_column(index=True)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None]
    ref: Mapped[str | None]
    files: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    occurred_at: Mapped[datetime]
    payload: Mapped[dict] = mapped_column(default=dict)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"))


class SessionCommitLink(Base):
    __tablename__ = "session_commit_links"
    __table_args__ = (UniqueConstraint("session_id", "git_object_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("code_sessions.session_id"))
    git_object_id: Mapped[str] = mapped_column(ForeignKey("git_objects.id"))
    confidence: Mapped[float] = mapped_column(Float)
    signals: Mapped[dict] = mapped_column(default=dict)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(primary_key=True)
    event_name: Mapped[str]
    received_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    actor: Mapped[str]
    action: Mapped[str]
    target: Mapped[str | None]
    details: Mapped[dict] = mapped_column(default=dict)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(primary_key=True)
    type: Mapped[str]
    sensitivity: Mapped[str]
    config: Mapped[dict] = mapped_column(default=dict)


class Artifact(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(primary_key=True)
    artifact_kind: Mapped[str] = mapped_column(default="document")
    original_filename: Mapped[str | None]
    title: Mapped[str | None]
    sensitivity: Mapped[str] = mapped_column(default="confidential-personal")
    primary_project_id: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (UniqueConstraint("sha256"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), index=True)
    sha256: Mapped[str]
    byte_size: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str | None]
    object_id: Mapped[str]
    text_extracted: Mapped[bool] = mapped_column(Boolean, default=False)
    parser: Mapped[str | None]
    source_meta: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class ArtifactChunk(Base):
    __tablename__ = "artifact_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal"),
        Index("ix_artifact_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("artifact_versions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text_content: Mapped[str] = mapped_column(Text)
    locator: Mapped[dict] = mapped_column(default=dict)
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(text_content, ''))", persisted=True),
        nullable=True,
    )


class ArtifactReference(Base):
    __tablename__ = "artifact_references"

    id: Mapped[str] = mapped_column(primary_key=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"))
    conversation_id: Mapped[str | None]
    message_id: Mapped[str | None]
    display_name: Mapped[str | None]
    resolution_status: Mapped[str] = mapped_column(default="unresolved_missing_binary")
    provider_reference: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(primary_key=True)
    client_name: Mapped[str | None]
    redirect_uris: Mapped[dict] = mapped_column(default=dict)  # {"uris": [...]}
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class OAuthCode(Base):
    __tablename__ = "oauth_codes"

    code: Mapped[str] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.client_id"))
    redirect_uri: Mapped[str]
    code_challenge: Mapped[str]
    code_challenge_method: Mapped[str] = mapped_column(default="S256")
    scopes: Mapped[dict] = mapped_column(default=dict)  # {"scopes": [...]}
    expires_at: Mapped[datetime]
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    token_hash: Mapped[str] = mapped_column(primary_key=True)
    kind: Mapped[str]  # "access" | "refresh"
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.client_id"))
    scopes: Mapped[dict] = mapped_column(default=dict)
    expires_at: Mapped[datetime]
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    paired_hash: Mapped[str | None]
