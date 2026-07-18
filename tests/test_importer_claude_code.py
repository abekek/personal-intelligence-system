from pathlib import Path

import sqlalchemy as sa

import pis.normalize.claude_code  # noqa: F401  (registers normalizer)
from pis.db.models import Event, Turn
from pis.importers.claude_code import build_events_for_transcript, import_root
from pis.ingest.service import ingest_events
from pis.policy.engine import PolicyEngine
from pis.schemas.events import CanonicalEvent

FIXTURE = Path("tests/fixtures/transcript_multiturn.jsonl")


def test_build_events_multiturn():
    events, warnings = build_events_for_transcript(FIXTURE)
    assert len(events) == 2

    first = CanonicalEvent.model_validate(events[0])
    assert first.provider_conversation_id == "sess-hist"
    assert first.metadata["cwd"] == "/Users/alibek/demo"
    assert first.metadata["changed_files"] == ["src/utils.py"]
    assert first.content_parts[0].text == "Add a retry helper to utils"
    assert first.content_parts[1].text == "Added retry helper with exponential backoff."
    assert first.occurred_at.isoformat().startswith("2026-07-01T10:00:09")
    assert first.capture_method == "export_import"

    # sidechain + unknown records counted as skipped, not silently dropped
    assert warnings["sidechain_skipped"] == 1
    assert warnings["unsupported_records"] == 1


def test_build_events_redacts_secrets():
    events, _ = build_events_for_transcript(FIXTURE)
    second = CanonicalEvent.model_validate(events[1])
    assert "supersecret123" not in second.content_parts[0].text
    assert "[REDACTED:password_assignment]" in second.content_parts[0].text


def test_deterministic_hashes_for_reimport():
    a, _ = build_events_for_transcript(FIXTURE)
    b, _ = build_events_for_transcript(FIXTURE)
    hashes_a = [CanonicalEvent.model_validate(e).content_hash for e in a]
    hashes_b = [CanonicalEvent.model_validate(e).content_hash for e in b]
    assert hashes_a == hashes_b


def test_import_root_is_idempotent(db, tmp_path):
    root = tmp_path / "projects" / "-Users-alibek-demo"
    root.mkdir(parents=True)
    (root / "sess-hist.jsonl").write_text(FIXTURE.read_text())

    policy = PolicyEngine.load(Path("config"))

    def sender(events):
        return ingest_events(db, [CanonicalEvent.model_validate(e) for e in events], policy)

    manifest = import_root(tmp_path / "projects", sender)
    assert manifest["transcripts_seen"] == 1
    assert manifest["events_built"] == 2
    assert manifest["created"] == 2
    assert db.scalar(sa.select(sa.func.count()).select_from(Turn)) == 2

    manifest2 = import_root(tmp_path / "projects", sender)
    assert manifest2["created"] == 0
    assert manifest2["duplicate"] == 2
    assert db.scalar(sa.select(sa.func.count()).select_from(Event)) == 2
