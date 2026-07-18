import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

HOOK_PATH = Path("integrations/claude-code/hooks/stop_hook.py")
FIXTURE = Path("tests/fixtures/transcript_basic.jsonl")


def load_hook():
    spec = importlib.util.spec_from_file_location("stop_hook", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_last_turn():
    turn = load_hook().parse_last_turn(str(FIXTURE))
    assert turn["user_prompt"] == "Add a retry helper to utils"
    assert turn["assistant_text"] == "Added retry helper with exponential backoff."
    assert turn["changed_files"] == ["src/utils.py"]
    assert turn["tool_uses"] == [{"tool_name": "Edit", "summary": {"file_path": "src/utils.py"}}]
    assert turn["occurred_at"] == "2026-07-18T12:00:09Z"


def test_build_event_matches_canonical_contract():
    hook = load_hook()
    turn = hook.parse_last_turn(str(FIXTURE))
    git = {"repo_root": "/r", "git_remote": "git@github.com:abekek/demo.git",
           "branch": "main", "head_commit": "a" * 40}
    event = hook.build_event({"session_id": "sess-abc", "cwd": "/r"}, git, turn)

    from pis.schemas.events import CanonicalEvent
    parsed = CanonicalEvent.model_validate(event)
    assert str(parsed.event_type) == "claude_code.turn.completed"
    assert parsed.provider_conversation_id == "sess-abc"
    assert parsed.metadata["branch"] == "main"
    assert parsed.metadata["changed_files"] == ["src/utils.py"]
    roles = [p.metadata.get("role") for p in parsed.content_parts]
    assert roles == ["user", "assistant"]


def test_hook_never_blocks_and_exits_zero_when_daemon_down():
    hook_input = json.dumps({"session_id": "sess-x", "cwd": ".",
                             "transcript_path": str(FIXTURE)})
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)], input=hook_input, text=True,
        capture_output=True, timeout=15,
        env={"PATH": "/usr/bin:/bin", "PIS_DAEMON_URL": "http://127.0.0.1:9/v1/capture"},
    )
    assert proc.returncode == 0
    assert time.monotonic() - start < 10
