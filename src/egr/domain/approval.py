"""Approval is a first-class object: nothing critical happens without it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import ApprovalStatus, Environment


class Approval(BaseModel):
    id: str
    action: str
    tool: str
    args: dict = Field(default_factory=dict)  # redacted copy
    requested_by: str  # agent id
    task_id: str | None = None
    step_id: str | None = None
    environment: Environment = Environment.DEVELOPMENT
    status: ApprovalStatus = ApprovalStatus.PENDING
    required_role: str | None = None
    reason: str = ""
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    result: Any = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def pending(self) -> bool:
        return self.status == ApprovalStatus.PENDING
