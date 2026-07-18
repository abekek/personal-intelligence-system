#!/usr/bin/env python3
"""Claude Code Stop hook -> local capture daemon.

Stdlib only; must run on system python3 (3.11). MUST never block Claude Code:
every failure path swallows the error and exits 0.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

def _local_env() -> dict:
    """Read ~/.pis/daemon.env (written by install-daemon.sh) so the hook and
    daemon share the local capture token without putting it in the repo."""
    values = {}
    try:
        with open(os.path.expanduser("~/.pis/daemon.env"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip()
    except Exception:
        pass
    return values


_ENV_FILE = _local_env()
DAEMON_URL = os.environ.get("PIS_DAEMON_URL", "http://127.0.0.1:8787/v1/capture")
DAEMON_TOKEN = (os.environ.get("PIS_DAEMON_TOKEN")
                or _ENV_FILE.get("PIS_DAEMON_TOKEN", "dev-daemon-token"))
FILE_TOOLS = {"Edit", "Write", "NotebookEdit"}


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def _is_real_user(entry):
    if entry.get("type") != "user":
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        has_result = any(b.get("type") == "tool_result" for b in content)
        has_text = any(b.get("type") == "text" for b in content)
        return has_text and not has_result
    return False


def parse_last_turn(transcript_path):
    entries = []
    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    last_user = None
    for i, entry in enumerate(entries):
        if _is_real_user(entry):
            last_user = i
    if last_user is None:
        return None

    assistant_text = ""
    tool_uses = []
    changed = set()
    occurred_at = entries[-1].get("timestamp")
    for entry in entries[last_user:]:
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "text" and block.get("text"):
                assistant_text = block["text"]
            elif block.get("type") == "tool_use":
                name = block.get("name", "unknown")
                tool_input = block.get("input") or {}
                summary = {}
                if tool_input.get("file_path"):
                    summary["file_path"] = tool_input["file_path"]
                tool_uses.append({"tool_name": name, "summary": summary})
                if name in FILE_TOOLS and tool_input.get("file_path"):
                    changed.add(tool_input["file_path"])

    return {
        "user_prompt": _text_of(entries[last_user].get("message", {}).get("content")),
        "assistant_text": assistant_text,
        "tool_uses": tool_uses,
        "changed_files": sorted(changed),
        "occurred_at": occurred_at,
    }


def _git(cwd, *args):
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                             text=True, timeout=2)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except Exception:
        return None


def git_metadata(cwd):
    return {
        "repo_root": _git(cwd, "rev-parse", "--show-toplevel"),
        "git_remote": _git(cwd, "remote", "get-url", "origin"),
        "branch": _git(cwd, "rev-parse", "--abbrev-ref", "HEAD"),
        "head_commit": _git(cwd, "rev-parse", "HEAD"),
    }


def build_event(hook_input, git, turn):
    session_id = hook_input.get("session_id", "unknown-session")
    occurred_at = turn.get("occurred_at") or datetime.now(timezone.utc).isoformat()
    return {
        "event_type": "claude_code.turn.completed",
        "provider": "claude_code",
        "provider_conversation_id": session_id,
        "occurred_at": occurred_at,
        "capture_method": "claude_code_hook",
        "content_parts": [
            {"type": "text", "text": turn["user_prompt"], "metadata": {"role": "user"}},
            {"type": "text", "text": turn["assistant_text"], "metadata": {"role": "assistant"}},
        ],
        "metadata": {
            "session_id": session_id,
            "cwd": hook_input.get("cwd"),
            "repo_root": git.get("repo_root"),
            "git_remote": git.get("git_remote"),
            "branch": git.get("branch"),
            "head_commit": git.get("head_commit"),
            "changed_files": turn["changed_files"],
            "tool_uses": turn["tool_uses"],
        },
    }


def main():
    try:
        hook_input = json.load(sys.stdin)
        transcript_path = hook_input.get("transcript_path")
        if not transcript_path or not os.path.exists(transcript_path):
            return
        turn = parse_last_turn(transcript_path)
        if turn is None:
            return
        git = git_metadata(hook_input.get("cwd") or ".")
        event = build_event(hook_input, git, turn)
        body = json.dumps({"events": [event]}).encode()
        request = urllib.request.Request(
            DAEMON_URL, data=body, method="POST",
            headers={"Content-Type": "application/json", "X-Capture-Token": DAEMON_TOKEN},
        )
        urllib.request.urlopen(request, timeout=2)
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
