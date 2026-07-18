from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from pis.db.models import Event

OCC = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def make_row(event_id="evt_1", content_hash="sha256:h1"):
    return Event(
        event_id=event_id, event_type="claude_code.turn.completed",
        provider="claude_code", occurred_at=OCC, captured_at=OCC,
        capture_method="claude_code_hook", content_hash=content_hash, payload={},
    )


def test_insert_and_read_event(db):
    db.add(make_row())
    db.commit()
    assert db.get(Event, "evt_1").provider == "claude_code"


def test_events_are_update_immutable(db):
    db.add(make_row())
    db.commit()
    with pytest.raises(sa.exc.DBAPIError, match="immutable"):
        db.execute(sa.text("UPDATE events SET provider = 'x' WHERE event_id = 'evt_1'"))
        db.commit()
    db.rollback()


def test_events_are_delete_immutable(db):
    db.add(make_row())
    db.commit()
    with pytest.raises(sa.exc.DBAPIError, match="immutable"):
        db.execute(sa.text("DELETE FROM events WHERE event_id = 'evt_1'"))
        db.commit()
    db.rollback()


def test_content_hash_unique(db):
    db.add(make_row())
    db.commit()
    db.add(make_row(event_id="evt_2", content_hash="sha256:h1"))
    with pytest.raises(sa.exc.IntegrityError):
        db.commit()
    db.rollback()
