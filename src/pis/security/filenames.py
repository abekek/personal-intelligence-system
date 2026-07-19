"""Filename-level deny list for artifact ingestion.

Content-level redaction (secrets.py) is the backstop; these files are
credentials *by construction*, so they must never be uploaded at all —
the raw bytes would land in the object store even if chunks are redacted.
"""
from __future__ import annotations

from fnmatch import fnmatch

DENIED_FILENAME_PATTERNS = [
    "*accesskeys*.csv", "*credentials*.csv",
    "*.pem", "*.key", "*.p12", "*.pfx",
    ".env", "*.env", ".env.*",
    "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "*.kdbx", "*secrets*.yaml", "*secrets*.yml", "*secrets*.json",
]


def is_denied_filename(filename: str) -> bool:
    name = filename.lower().rsplit("/", 1)[-1]
    return any(fnmatch(name, pattern) for pattern in DENIED_FILENAME_PATTERNS)
