"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""
from alembic import op

from pis.db.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

IMMUTABILITY_SQL = """
CREATE OR REPLACE FUNCTION forbid_event_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'events ledger is immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_immutable
BEFORE UPDATE OR DELETE ON events
FOR EACH ROW EXECUTE FUNCTION forbid_event_mutation();
"""


def _initial_tables():
    # 0001 owns only the pre-OAuth schema; later revisions create their own
    # tables explicitly (metadata reflects the current models, not history).
    return [t for name, t in Base.metadata.tables.items()
            if not name.startswith(("oauth_", "artifact"))]


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_initial_tables())
    op.execute(IMMUTABILITY_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS events_immutable ON events")
    op.execute("DROP FUNCTION IF EXISTS forbid_event_mutation")
    Base.metadata.drop_all(bind=op.get_bind(), tables=_initial_tables())
