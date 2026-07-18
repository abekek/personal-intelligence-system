"""memory items, evidence, extraction runs

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", JSONB(), nullable=False),
    )
    op.create_table(
        "memory_items",
        sa.Column("memory_id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("authority", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sensitivity", sa.String(), nullable=False),
        sa.Column("supersedes_memory_id", sa.String(), nullable=True),
        sa.Column("extraction_run_id", sa.String(),
                  sa.ForeignKey("extraction_runs.id"), nullable=True),
        sa.Column("source_conversation_id", sa.String(), nullable=True),
        sa.Column("tsv", TSVECTOR(),
                  sa.Computed("to_tsvector('english', coalesce(statement, ''))",
                              persisted=True), nullable=True),
    )
    op.create_index("ix_memory_items_tsv", "memory_items", ["tsv"],
                    postgresql_using="gin")
    op.execute("ALTER TABLE memory_items ADD COLUMN embedding vector(1024)")
    op.execute("CREATE INDEX ix_memory_items_embedding ON memory_items "
               "USING hnsw (embedding vector_cosine_ops)")
    op.create_table(
        "memory_evidence",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("memory_id", sa.String(),
                  sa.ForeignKey("memory_items.memory_id"), nullable=False, index=True),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
    )
    op.add_column("conversations",
                  sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "extracted_at")
    op.drop_table("memory_evidence")
    op.drop_table("memory_items")
    op.drop_table("extraction_runs")
