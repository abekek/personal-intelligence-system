import pytest

from pis.security.secrets import contains_secret, redact_text, scan_text

SAMPLES = [
    ("anthropic_key", "key is sk-ant-api03-abcdefghijklmnop123456"),
    ("aws_access_key", "AKIAIOSFODNN7EXAMPLE creds"),
    ("github_token", "token ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("private_key", "-----BEGIN RSA PRIVATE KEY-----"),
    ("password_assignment", "password = hunter22secret"),
    ("connection_string", "postgres://admin:s3cret@db.example.com:5432/app"),
    ("ssn", "SSN: 123-45-6789"),
]


@pytest.mark.parametrize("kind,text", SAMPLES)
def test_detects_each_kind(kind, text):
    assert kind in {m.kind for m in scan_text(text)}


def test_clean_text_has_no_matches():
    assert scan_text("Added retry helper with exponential backoff to utils.") == []
    assert not contains_secret("plain discussion of code")


def test_redact_replaces_span():
    out = redact_text("key is AKIAIOSFODNN7EXAMPLE ok")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:aws_access_key]" in out
    assert out.startswith("key is ") and out.endswith(" ok")
