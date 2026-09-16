"""Identifier generation.

IDs are prefixed and time-ordered so they sort roughly by creation time while
still being unguessable enough for audit references.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

PREFIXES = {
    "enterprise": "ent",
    "agent": "agt",
    "task": "tsk",
    "workflow": "wfl",
    "policy": "pol",
    "approval": "apr",
    "event": "evt",
    "artifact": "art",
    "memory": "mem",
    "principal": "prn",
    "token": "tkn",
    "secret": "scr",
    "key": "key",
    "run": "run",
    "extension": "ext",
    "proposal": "prp",
}


def new_id(kind: str) -> str:
    prefix = PREFIXES.get(kind, kind[:3])
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{stamp}_{secrets.token_hex(3)}"
