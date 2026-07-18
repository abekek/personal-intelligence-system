import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from pis.api.app import create_app
from pis.config import Settings
from pis.oauth.service import verify_access

SETTINGS = Settings(
    database_url="postgresql+psycopg://pis:pis@127.0.0.1:5433/pis_test",
    public_url="https://pis.example.com",
    oauth_passcode="test-passcode-123",
)


def client_app():
    return TestClient(create_app(SETTINGS))


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def full_dance(client, passcode="test-passcode-123"):
    reg = client.post("/register", json={
        "client_name": "claude.ai", "redirect_uris": ["https://claude.ai/api/mcp/callback"],
    })
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]

    verifier, challenge = pkce_pair()
    submit = client.post("/authorize", data={
        "client_id": client_id, "redirect_uri": "https://claude.ai/api/mcp/callback",
        "state": "xyz", "code_challenge": challenge,
        "code_challenge_method": "S256", "scope": "kb", "passcode": passcode,
    }, follow_redirects=False)
    if submit.status_code != 302:
        return submit, None, None
    location = submit.headers["location"]
    query = parse_qs(urlparse(location).query)
    assert query["state"] == ["xyz"]
    code = query["code"][0]

    token = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "https://claude.ai/api/mcp/callback",
        "client_id": client_id, "code_verifier": verifier,
    })
    return submit, client_id, token


def test_metadata_discovery(engine, db):
    r = client_app().get("/.well-known/oauth-authorization-server")
    body = r.json()
    assert body["issuer"] == "https://pis.example.com"
    assert body["registration_endpoint"].endswith("/register")
    assert "S256" in body["code_challenge_methods_supported"]


def test_full_dance_and_verify(engine, db):
    client = client_app()
    _, client_id, token = full_dance(client)
    assert token.status_code == 200
    body = token.json()
    assert body["access_token"].startswith("pis_at_")
    info = verify_access(db, body["access_token"])
    assert info is not None and info.scopes == ["kb"]

    # refresh rotates and revokes the old pair
    refreshed = client.post("/token", data={
        "grant_type": "refresh_token", "refresh_token": body["refresh_token"],
        "client_id": client_id,
    })
    assert refreshed.status_code == 200
    assert verify_access(db, body["access_token"]) is None
    assert verify_access(db, refreshed.json()["access_token"]) is not None


def test_wrong_passcode_rejected(engine, db):
    submit, _, _ = full_dance(client_app(), passcode="wrong")
    assert submit.status_code == 403


def test_bad_verifier_and_code_reuse_rejected(engine, db):
    client = client_app()
    reg = client.post("/register", json={
        "client_name": "x", "redirect_uris": ["https://cb.example/cb"],
    })
    client_id = reg.json()["client_id"]
    verifier, challenge = pkce_pair()
    submit = client.post("/authorize", data={
        "client_id": client_id, "redirect_uri": "https://cb.example/cb",
        "code_challenge": challenge, "code_challenge_method": "S256",
        "scope": "kb", "passcode": "test-passcode-123",
    }, follow_redirects=False)
    code = parse_qs(urlparse(submit.headers["location"]).query)["code"][0]

    bad = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "https://cb.example/cb", "client_id": client_id,
        "code_verifier": "not-the-verifier-not-the-verifier-not",
    })
    assert bad.status_code == 400

    good_then_reuse = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "https://cb.example/cb", "client_id": client_id,
        "code_verifier": verifier,
    })
    # the failed attempt above did not consume the code
    assert good_then_reuse.status_code == 200
    reuse = client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "https://cb.example/cb", "client_id": client_id,
        "code_verifier": verifier,
    })
    assert reuse.status_code == 400
