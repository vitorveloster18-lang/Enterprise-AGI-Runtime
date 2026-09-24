"""Workflow run: a execução como objeto de primeira classe.

Um workflow é a receita (versionada em YAML). O **run** é o fato: quem disparou,
em que ambiente, o que cada passo produziu, quanto custou e por que parou.

Sem isso, orquestração é só um laço `for` invisível — impossível de auditar,
retomar ou explicar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import Environment, RunStatus, StepRunStatus


class WorkflowStepRun(BaseModel):
    id: str  # id do passo no workflow
    status: StepRunStatus = StepRunStatus.PENDING
    task_id: str | None = None
    attempts: int = 0
    error: str | None = None
    outputs: dict = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {
            StepRunStatus.COMPLETED,
            StepRunStatus.FAILED,
            StepRunStatus.SKIPPED,
            StepRunStatus.CANCELLED,
        }

    def as_row(self) -> dict[str, Any]:
        return {
            "passo": self.id,
            "status": str(self.status),
            "task": self.task_id or "-",
            "tentativas": self.attempts,
            "erro": self.error or "-",
        }


class WorkflowRun(BaseModel):
    id: str
    workflow_id: str
    workflow_version: str = "1.0.0"
    environment: Environment | str = Environment.DEVELOPMENT
    status: RunStatus = RunStatus.PENDING
    trigger: str = "manual"  # manual | event | cron | webhook
    trigger_detail: str = ""
    inputs: dict = Field(default_factory=dict)
    #: saídas por passo: {"s1": {"status": ..., "answer": ..., "outputs": {...}}}
    context: dict = Field(default_factory=dict)
    steps: list[WorkflowStepRun] = Field(default_factory=list)
    created_by: str = "cli"
    error: str | None = None
    cost: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def step(self, step_id: str) -> WorkflowStepRun | None:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def ensure_step(self, step_id: str) -> WorkflowStepRun:
        found = self.step(step_id)
        if found is None:
            found = WorkflowStepRun(id=step_id)
            self.steps.append(found)
        return found

    @property
    def finished(self) -> bool:
        return self.status in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }

    @property
    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.steps),
            "completed": len([step for step in self.steps if str(step.status) == "completed"]),
            "failed": len([step for step in self.steps if str(step.status) == "failed"]),
            "skipped": len([step for step in self.steps if str(step.status) == "skipped"]),
            "waiting": len([step for step in self.steps if str(step.status) == "waiting"]),
        }

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflow": f"{self.workflow_id}@{self.workflow_version}",
            "ambiente": str(self.environment),
            "status": str(self.status),
            "trigger": f"{self.trigger}{f': {self.trigger_detail}' if self.trigger_detail else ''}",
            "passos": f"{self.summary['completed']}/{self.summary['total']}",
            "custo": round(self.cost, 6),
            "criado em": self.created_at.strftime("%Y-%m-%d %H:%M"),
        }


__all__ = ["WorkflowRun", "WorkflowStepRun"]
