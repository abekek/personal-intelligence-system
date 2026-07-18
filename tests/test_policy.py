from datetime import datetime, timezone
from pathlib import Path

from pis.policy.engine import PolicyEngine
from pis.schemas.events import CanonicalEvent, EventType

OCC = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def make_event(**metadata):
    return CanonicalEvent(
        event_type=EventType.CC_TURN_COMPLETED,
        provider="claude_code",
        occurred_at=OCC,
        capture_method="claude_code_hook",
        metadata=metadata,
    )


def engine():
    return PolicyEngine.load(Path("config"))


def test_loads_projects():
    assert "meta/personal-intelligence-system" in engine().projects


def test_denied_path_patterns():
    e = engine()
    assert e.is_denied_path("/Users/alibek/project/.env")
    assert e.is_denied_path("/tmp/secrets/key.txt")
    assert not e.is_denied_path("/Users/alibek/project/src/main.py")


def test_denied_repo_patterns():
    e = engine()
    assert e.is_denied_repo("git@github.com:employer-org/internal.git")
    assert not e.is_denied_repo("git@github.com:abekek/demo.git")


def test_check_event_rejects_denied_repo():
    reason = engine().check_event(make_event(git_remote="git@github.com:employer-org/x.git"))
    assert reason == "denied_repository"


def test_check_event_rejects_denied_changed_file():
    reason = engine().check_event(make_event(changed_files=["/home/x/app/.env"]))
    assert reason == "denied_path"


def test_check_event_allows_clean_event():
    assert engine().check_event(make_event(git_remote="git@github.com:abekek/demo.git")) is None
