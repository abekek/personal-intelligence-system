"""Live E2E: OAuth dance + MCP tools against the deployed service.

Usage: uv run python scripts/verify_mcp_live.py <base_url> <passcode>
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import httpx


def rpc_body(text: str) -> dict:
    """Parse either JSON or SSE-wrapped JSON-RPC response bodies."""
    if text.lstrip().startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise ValueError(f"no JSON in response: {text[:200]}")


def main() -> int:
    base = sys.argv[1].rstrip("/")
    passcode = sys.argv[2]
    client = httpx.Client(timeout=30.0)

    meta = client.get(f"{base}/.well-known/oauth-protected-resource/mcp").json()
    assert meta["authorization_servers"] == [base], meta
    as_meta = client.get(f"{base}/.well-known/oauth-authorization-server").json()
    print("discovery ok:", as_meta["authorization_endpoint"])

    reg = client.post(as_meta["registration_endpoint"], json={
        "client_name": "e2e-verify", "redirect_uris": ["https://claude.ai/api/mcp/callback"],
    })
    client_id = reg.json()["client_id"]

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    submit = client.post(as_meta["authorization_endpoint"], data={
        "client_id": client_id, "redirect_uri": "https://claude.ai/api/mcp/callback",
        "state": "e2e", "code_challenge": challenge, "code_challenge_method": "S256",
        "scope": "kb", "passcode": passcode,
    }, follow_redirects=False)
    assert submit.status_code == 302, submit.text
    code = parse_qs(urlparse(submit.headers["location"]).query)["code"][0]

    token = client.post(as_meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "https://claude.ai/api/mcp/callback",
        "client_id": client_id, "code_verifier": verifier,
    }).json()
    access = token["access_token"]
    print("oauth ok: token acquired")

    headers = {
        "Authorization": f"Bearer {access}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    unauth = client.post(f"{base}/mcp", json={})
    assert unauth.status_code == 401, unauth.status_code
    print("401 without token ok")

    def call(method: str, params: dict, rpc_id: int) -> dict:
        response = client.post(f"{base}/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params,
        })
        assert response.status_code == 200, f"{method}: {response.status_code} {response.text[:200]}"
        return rpc_body(response.text)

    init = call("initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "e2e", "version": "0"},
    }, 1)
    assert init["result"]["serverInfo"]["name"] == "pis-knowledge"
    print("initialize ok")

    tools = call("tools/list", {}, 2)
    names = [t["name"] for t in tools["result"]["tools"]]
    assert "kb_capture_note" in names and "kb_search" in names, names
    print("tools:", names)

    note = call("tools/call", {
        "name": "kb_capture_note",
        "arguments": {"note": "E2E verification note: MCP phase live",
                      "conversation_key": "pis/e2e"},
    }, 3)
    payload = json.loads(note["result"]["content"][0]["text"])
    assert payload["status"] in ("created", "duplicate"), payload
    print("capture ok:", payload["status"], payload["conversation_id"])

    found = call("tools/call", {
        "name": "kb_search",
        "arguments": {"query": "E2E verification note MCP phase"},
    }, 4)
    hits = json.loads(found["result"]["content"][0]["text"])
    assert hits, "capture note not found via search"
    print("search-after-capture ok")
    print("ALL LIVE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
