"""Workflow: how work happens. (Agent = who works. Workflow = how.)

Declared now (Phase 0), executed sequentially from Phase 6 onwards.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import Environment


class WorkflowTrigger(BaseModel):
    type: str = "manual"  # manual | event | cron | webhook | message
    event: str | None = None
    cron: str | None = None


class WorkflowStep(BaseModel):
    id: str
    agent: str | None = None
    task: str | None = None  # named task template
    objective: str | None = None
    tool: str | None = None
    args: dict = Field(default_factory=dict)
    policy: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class Workflow(BaseModel):
    id: str
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    environment: Environment = Environment.DEVELOPMENT
    trigger: WorkflowTrigger = Field(default_factory=WorkflowTrigger)
    steps: list[WorkflowStep] = Field(default_factory=list)
