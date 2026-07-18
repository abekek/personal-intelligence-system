"""Minimal single-user OAuth 2.1 authorization server (PKCE + DCR + refresh).

Tokens are opaque (`pis_at_`/`pis_rt_` + 48 hex) and stored hashed; the
consent step is a passcode gate — there is exactly one user.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from pis.db.models import OAuthClient, OAuthCode, OAuthToken

CODE_TTL = timedelta(minutes=10)
ACCESS_TTL = timedelta(days=30)
REFRESH_TTL = timedelta(days=90)
SCOPES = ["kb"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register_client(db: Session, client_name: str | None, redirect_uris: list[str]) -> OAuthClient:
    client = OAuthClient(
        client_id="pis_client_" + secrets.token_hex(16),
        client_name=client_name,
        redirect_uris={"uris": redirect_uris},
    )
    db.add(client)
    db.commit()
    return client


def issue_code(
    db: Session, client_id: str, redirect_uri: str,
    code_challenge: str, code_challenge_method: str, scopes: list[str],
) -> str:
    client = db.get(OAuthClient, client_id)
    if client is None or redirect_uri not in client.redirect_uris.get("uris", []):
        raise ValueError("unknown client or redirect_uri")
    if code_challenge_method != "S256":
        raise ValueError("only S256 supported")
    code = "pis_code_" + secrets.token_hex(24)
    db.add(OAuthCode(
        code=code, client_id=client_id, redirect_uri=redirect_uri,
        code_challenge=code_challenge, code_challenge_method=code_challenge_method,
        scopes={"scopes": scopes}, expires_at=_now() + CODE_TTL, used=False,
    ))
    db.commit()
    return code


def _verify_pkce(challenge: str, verifier: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(expected, challenge)


def _mint_pair(db: Session, client_id: str, scopes: list[str]) -> dict:
    access = "pis_at_" + secrets.token_hex(24)
    refresh = "pis_rt_" + secrets.token_hex(24)
    db.add(OAuthToken(token_hash=_hash(access), kind="access", client_id=client_id,
                      scopes={"scopes": scopes}, expires_at=_now() + ACCESS_TTL,
                      revoked=False, paired_hash=_hash(refresh)))
    db.add(OAuthToken(token_hash=_hash(refresh), kind="refresh", client_id=client_id,
                      scopes={"scopes": scopes}, expires_at=_now() + REFRESH_TTL,
                      revoked=False, paired_hash=_hash(access)))
    db.commit()
    return {
        "access_token": access, "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh, "scope": " ".join(scopes),
    }


def exchange_code(
    db: Session, code: str, client_id: str, redirect_uri: str, code_verifier: str,
) -> dict:
    row = db.get(OAuthCode, code)
    if (row is None or row.used or row.client_id != client_id
            or row.redirect_uri != redirect_uri or row.expires_at < _now()):
        raise ValueError("invalid_grant")
    if not _verify_pkce(row.code_challenge, code_verifier):
        raise ValueError("invalid_grant")
    row.used = True
    db.flush()
    return _mint_pair(db, client_id, row.scopes.get("scopes", SCOPES))


def refresh_grant(db: Session, refresh_token: str, client_id: str) -> dict:
    row = db.get(OAuthToken, _hash(refresh_token))
    if (row is None or row.kind != "refresh" or row.revoked
            or row.client_id != client_id or row.expires_at < _now()):
        raise ValueError("invalid_grant")
    row.revoked = True
    if row.paired_hash:
        paired = db.get(OAuthToken, row.paired_hash)
        if paired is not None:
            paired.revoked = True
    db.flush()
    return _mint_pair(db, client_id, row.scopes.get("scopes", SCOPES))


@dataclass
class AccessInfo:
    client_id: str
    scopes: list[str]


def verify_access(db: Session, token: str) -> AccessInfo | None:
    row = db.get(OAuthToken, _hash(token))
    if row is None or row.kind != "access" or row.revoked or row.expires_at < _now():
        return None
    return AccessInfo(client_id=row.client_id, scopes=row.scopes.get("scopes", []))
