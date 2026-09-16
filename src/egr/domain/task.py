"""Task: a unit of work executed by an agent inside an environment."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import Environment, TaskStatus


class StepRecord(BaseModel):
    id: str
    tool: str
    args: dict = Field(default_factory=dict)
    rationale: str = ""
    decision: str | None = None
    rule_id: str | None = None
    approval_id: str | None = None
    ok: bool | None = None
    output: Any = None
    error: str | None = None
    duration_ms: int = 0


class TaskResult(BaseModel):
    answer: str = ""
    steps: list[StepRecord] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    plan: dict = Field(default_factory=dict)
    model: str | None = None
    provider: str | None = None
    duration_ms: int = 0
    error: str | None = None
    # Fase 2: custo é cidadão de primeira classe (base da medição de ROI)
    cost: float = 0.0
    tokens: dict = Field(default_factory=dict)  # {"input": n, "output": n}
    model_calls: int = 0


class Task(BaseModel):
    id: str
    objective: str
    agent_id: str
    environment: Environment = Environment.DEVELOPMENT
    status: TaskStatus = TaskStatus.PENDING
    parent_id: str | None = None
    created_by: str = "cli"
    workflow_id: str | None = None
    context: dict = Field(default_factory=dict)  # plan, cursor, approvals, etc.
    result: TaskResult | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
