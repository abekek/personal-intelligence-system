from fastapi.testclient import TestClient

from pis.config import Settings
from pis.daemon.app import create_daemon_app
from pis.daemon.outbox import Outbox

EVENT = {
    "event_type": "claude_code.turn.completed",
    "provider": "claude_code",
    "provider_conversation_id": "sess-1",
    "occurred_at": "2026-07-18T12:00:00Z",
    "capture_method": "claude_code_hook",
    "content_parts": [{"type": "text", "text": "key AKIAIOSFODNN7EXAMPLE here"}],
    "metadata": {"session_id": "sess-1"},
}


def daemon(tmp_path, post_fn):
    settings = Settings(daemon_outbox_path=tmp_path / "outbox.sqlite3")
    app = create_daemon_app(settings, post_fn=post_fn)
    return TestClient(app), settings, app


def test_capture_requires_token(tmp_path):
    client, _, _ = daemon(tmp_path, post_fn=lambda body: True)
    assert client.post("/v1/capture", json={"events": [EVENT]}).status_code == 401


def test_capture_redacts_and_buffers_when_api_down(tmp_path):
    client, settings, app = daemon(tmp_path, post_fn=lambda body: False)
    r = client.post("/v1/capture", json={"events": [EVENT]},
                    headers={"X-Capture-Token": settings.daemon_token})
    assert r.status_code == 200
    [item] = app.state.outbox.pending()
    stored_text = item.body["events"][0]["content_parts"][0]["text"]
    assert "AKIAIOSFODNN7EXAMPLE" not in stored_text
    assert "[REDACTED:aws_access_key]" in stored_text


def test_flush_drains_outbox_after_recovery(tmp_path):
    sent = []
    healthy = {"up": False}

    def post_fn(body):
        if healthy["up"]:
            sent.append(body)
            return True
        return False

    client, settings, app = daemon(tmp_path, post_fn)
    headers = {"X-Capture-Token": settings.daemon_token}
    client.post("/v1/capture", json={"events": [EVENT]}, headers=headers)
    client.post("/v1/capture", json={"events": [EVENT]}, headers=headers)
    assert len(app.state.outbox.pending()) == 2

    healthy["up"] = True
    r = client.post("/v1/flush", headers=headers)
    assert r.json()["sent"] == 2
    assert app.state.outbox.pending() == []
    assert len(sent) == 2


def test_outbox_persists_across_instances(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    Outbox(path).enqueue({"events": []})
    assert len(Outbox(path).pending()) == 1
