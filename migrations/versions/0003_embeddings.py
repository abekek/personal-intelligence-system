"""pgvector embeddings

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-18
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE message_revisions ADD COLUMN embedding vector(1024)")
    op.execute("ALTER TABLE turns ADD COLUMN embedding vector(1024)")
    op.execute(
        "CREATE INDEX ix_message_revisions_embedding ON message_revisions "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_turns_embedding ON turns "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_turns_embedding")
    op.execute("DROP INDEX IF EXISTS ix_message_revisions_embedding")
    op.execute("ALTER TABLE turns DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE message_revisions DROP COLUMN IF EXISTS embedding")
