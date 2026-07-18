from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("password_assignment", re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[=:]\s*\S{4,}")),
    ("connection_string", re.compile(r"\b[a-z][a-z0-9+]*://[^\s:@/]+:[^\s@/]+@\S+")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]


@dataclass(frozen=True)
class SecretMatch:
    kind: str
    start: int
    end: int


def scan_text(text: str) -> list[SecretMatch]:
    matches: list[SecretMatch] = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            matches.append(SecretMatch(kind, m.start(), m.end()))
    return matches


def contains_secret(text: str) -> bool:
    return bool(scan_text(text))


def redact_text(text: str) -> str:
    for m in sorted(scan_text(text), key=lambda m: m.start, reverse=True):
        text = text[: m.start] + f"[REDACTED:{m.kind}]" + text[m.end:]
    return text
