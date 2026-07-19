import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from pis.db.models import Base

TEST_DB_URL = os.environ.get(
    "PIS_TEST_DATABASE_URL", "postgresql+psycopg://pis:pis@127.0.0.1:5433/pis_test"
)


@pytest.fixture(scope="session")
def engine():
    engine = sa.create_engine(TEST_DB_URL)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    # migration-only tables (not in Base.metadata) must be truncated too
    extra = ["memory_items", "memory_evidence", "extraction_runs"]
    tables = ", ".join([t.name for t in Base.metadata.sorted_tables] + extra)
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
