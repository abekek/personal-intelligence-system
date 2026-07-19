#!/usr/bin/env python3
"""Claude Code SessionStart hook: inject a compact context pack for the
current repository from the personal knowledge ledger.

Stdlib only, python3.11-safe, 3s budget, always exits 0. Whatever this
prints to stdout becomes session context; printing nothing is a safe no-op.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

MAX_CHARS = 1800


def _local_env():
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


def format_pack(repo: str, pack: dict) -> str:
    memories = pack.get("memories") or []
    if not memories:
        return ""
    lines = [f"[Personal Ledger] Recent knowledge for {repo} "
             "(distilled from past sessions; evidence available via kb_search):"]
    for memory in memories[:7]:
        line = f"- ({memory.get('kind', '?')}) {memory.get('statement', '')}"
        if sum(len(l) for l in lines) + len(line) > MAX_CHARS:
            break
        lines.append(line)
    return "\n".join(lines)


def main():
    try:
        hook_input = json.load(sys.stdin)
        cwd = hook_input.get("cwd") or os.getcwd()
        repo = os.path.basename(cwd.rstrip("/")) or "this project"
        env = _local_env()
        api_url = env.get("PIS_API_URL")
        token = env.get("PIS_INGEST_TOKEN")
        if not api_url or not token:
            return
        topic = f"{repo} recent decisions, open tasks, current state"
        url = api_url.rstrip("/") + "/v1/context-pack?topic=" + urllib.parse.quote(topic)
        request = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=3) as response:
            pack = json.loads(response.read())
        text = format_pack(repo, pack)
        if text:
            print(text)
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
