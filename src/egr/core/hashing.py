"""Canonical JSON + hashing used by the audit ledger hash-chain."""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "0" * 64


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def chain_hash(previous: str | None, payload: Any) -> str:
    return sha256(f"{previous or GENESIS}|{canonical_json(payload)}")
