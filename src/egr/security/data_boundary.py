"""Data Boundary: classification / minimization / sanitization before data leaves.

    LOCAL DATA -> CLASSIFICATION -> MINIMIZATION -> SANITIZATION -> POLICY -> EXTERNAL API
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PATTERNS: dict[str, re.Pattern] = {
    "cpf": re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    "cnpj": re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"),
    "phone_br": re.compile(r"\b\(?\d{2}\)?\s?9?\d{4}-?\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
    "pix_key": re.compile(r"\b(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.IGNORECASE),
}


@dataclass
class BoundaryResult:
    allowed: bool
    labels: list[str] = field(default_factory=list)
    sanitized: Any = None
    reason: str = ""
    modified: bool = False


def classify(text: str) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    return sorted(label for label, pattern in PATTERNS.items() if pattern.search(text))


def sanitize(text: str) -> tuple[str, list[str]]:
    labels: list[str] = []
    result = text
    for label, pattern in PATTERNS.items():
        if pattern.search(result):
            labels.append(label)
            result = pattern.sub(f"[{label.upper()}_REMOVED]", result)
    return result, labels


def sanitize_payload(payload: Any) -> tuple[Any, list[str]]:
    if isinstance(payload, str):
        return sanitize(payload)
    if isinstance(payload, list):
        labels: list[str] = []
        items = []
        for item in payload:
            cleaned, found = sanitize_payload(item)
            labels.extend(found)
            items.append(cleaned)
        return items, sorted(set(labels))
    if isinstance(payload, dict):
        labels = []
        cleaned = {}
        for key, value in payload.items():
            new_value, found = sanitize_payload(value)
            labels.extend(found)
            cleaned[key] = new_value
        return cleaned, sorted(set(labels))
    return payload, []


def check_external(payload: Any, external_ai: str = "allowed") -> BoundaryResult:
    """Decide whether `payload` may cross the enterprise boundary."""
    labels = classify(payload if isinstance(payload, str) else str(payload))
    if external_ai == "forbidden":
        return BoundaryResult(allowed=False, labels=labels, reason="external AI is forbidden by policy")
    if external_ai == "restricted":
        cleaned, found = sanitize_payload(payload)
        return BoundaryResult(
            allowed=True,
            labels=sorted(set(labels + found)),
            sanitized=cleaned,
            reason="payload sanitized before leaving the boundary",
            modified=bool(found),
        )
    return BoundaryResult(allowed=True, labels=labels, sanitized=payload, reason="external AI allowed")
