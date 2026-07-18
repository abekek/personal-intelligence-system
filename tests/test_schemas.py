from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from pis.schemas.events import CanonicalEvent, ContentPart, EventType

OCC = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def make_event(**kw):
    defaults = dict(
        event_type=EventType.CC_TURN_COMPLETED,
        provider="claude_code",
        provider_conversation_id="sess-1",
        occurred_at=OCC,
        capture_method="claude_code_hook",
        content_parts=[ContentPart(type="text", text="hello", metadata={"role": "user"})],
    )
    defaults.update(kw)
    return CanonicalEvent(**defaults)


def test_event_id_and_hash_are_generated():
    ev = make_event()
    assert ev.event_id.startswith("evt_")
    assert ev.content_hash.startswith("sha256:")


def test_content_hash_deterministic_and_content_sensitive():
    assert make_event().content_hash == make_event().content_hash
    other = make_event(content_parts=[ContentPart(type="text", text="different")])
    assert other.content_hash != make_event().content_hash


def test_metadata_changes_hash():
    a = make_event(metadata={"delivery_id": "1"})
    b = make_event(metadata={"delivery_id": "2"})
    assert a.content_hash != b.content_hash


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        make_event(bogus=1)


def test_unknown_event_type_rejected():
    with pytest.raises(ValidationError):
        make_event(event_type="not.a.type")


def test_supplied_hash_preserved():
    ev = make_event(content_hash="sha256:abc")
    assert ev.content_hash == "sha256:abc"
