"""Secret redaction for approvals, logs and audit payloads."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "client_secret",
    "refresh_token",
}

REDACTED = "***redacted***"

_PATTERNS = [
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/=]{8,}"),
    re.compile(r"(?i)\b(sk|pk|api)[-_][A-Za-z0-9]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
]


def redact_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEYS)


def redact_mapping(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {}
    result = {}
    for key, value in data.items():
        if redact_key(str(key)):
            result[key] = REDACTED
        elif isinstance(value, dict):
            result[key] = redact_mapping(value)
        elif isinstance(value, list):
            result[key] = [redact_mapping(item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, str):
            result[key] = redact_text(value)
        else:
            result[key] = value
    return result


def redact_text(text: str) -> str:
    result = text
    for pattern in _PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result
