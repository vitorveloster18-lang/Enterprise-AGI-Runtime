"""Secrets never live in config files or in the database.

Resolution order:
  1. environment variable named by `api_key_env`
  2. optional .env file at the workspace root (never committed)
"""

from __future__ import annotations

import os
from pathlib import Path

from .redaction import REDACTED


def load_dotenv(path: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
        loaded[key] = value
    return loaded


def resolve_secret(api_key_env: str | None) -> str | None:
    if not api_key_env:
        return None
    return os.environ.get(api_key_env)


def mask(value: str | None) -> str:
    if not value:
        return REDACTED
    return f"{value[:2]}...{value[-2:]}" if len(value) > 8 else REDACTED
