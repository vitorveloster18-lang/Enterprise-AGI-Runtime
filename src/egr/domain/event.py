"""Event: the audit atom. Every relevant execution emits one."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import Environment, EventType


class Event(BaseModel):
    id: str
    seq: int | None = None  # monotonic, assigned by the ledger
    type: EventType | str
    actor: str = "runtime"
    task_id: str | None = None
    agent_id: str | None = None
    environment: Environment | str = Environment.DEVELOPMENT
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    prev_hash: str | None = None
    hash: str | None = None
