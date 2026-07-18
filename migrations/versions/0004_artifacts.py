"""artifact tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(), primary_key=True),
        sa.Column("artifact_kind", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("sensitivity", sa.String(), nullable=False),
        sa.Column("primary_project_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("artifact_id", sa.String(),
                  sa.ForeignKey("artifacts.artifact_id"), nullable=False, index=True),
        sa.Column("sha256", sa.String(), nullable=False, unique=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("text_extracted", sa.Boolean(), nullable=False),
        sa.Column("parser", sa.String(), nullable=True),
        sa.Column("source_meta", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "artifact_chunks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("version_id", sa.String(),
                  sa.ForeignKey("artifact_versions.id"), nullable=False, index=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("locator", JSONB(), nullable=False),
        sa.Column("tsv", TSVECTOR(),
                  sa.Computed("to_tsvector('english', coalesce(text_content, ''))",
                              persisted=True), nullable=True),
        sa.UniqueConstraint("version_id", "ordinal"),
    )
    op.create_index("ix_artifact_chunks_tsv", "artifact_chunks", ["tsv"],
                    postgresql_using="gin")
    op.execute("ALTER TABLE artifact_chunks ADD COLUMN embedding vector(1024)")
    op.execute("CREATE INDEX ix_artifact_chunks_embedding ON artifact_chunks "
               "USING hnsw (embedding vector_cosine_ops)")
    op.create_table(
        "artifact_references",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("artifact_id", sa.String(),
                  sa.ForeignKey("artifacts.artifact_id"), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("resolution_status", sa.String(), nullable=False),
        sa.Column("provider_reference", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("artifact_references")
    op.drop_table("artifact_chunks")
    op.drop_table("artifact_versions")
    op.drop_table("artifacts")
