"""Tool: the boundary between the Agent and the world.

Agent asks -> Runtime authorizes -> Tool executes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .enums import Environment, RiskLevel


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict = Field(default_factory=dict)  # JSON-schema-ish: {name: {type, required, help}}
    risk: RiskLevel = RiskLevel.LOW
    side_effects: bool = False
    requires_network: bool = False
    tags: list[str] = Field(default_factory=list)


class ToolRequest(BaseModel):
    """An action proposal coming from an agent."""

    tool: str
    args: dict = Field(default_factory=dict)
    action: str | None = None  # semantic action used by policies (e.g. payment.create)
    task_id: str | None = None
    agent_id: str | None = None
    step_id: str | None = None
    environment: Environment = Environment.DEVELOPMENT
    rationale: str = ""
    metadata: dict = Field(default_factory=dict)

    @property
    def policy_action(self) -> str:
        return self.action or self.tool


class ToolResult(BaseModel):
    ok: bool
    output: Any = None
    error: str | None = None
    artifacts: list[dict] = Field(default_factory=list)
    duration_ms: int = 0
    metadata: dict = Field(default_factory=dict)

    @classmethod
    def failure(cls, error: str, **kwargs) -> ToolResult:
        return cls(ok=False, error=error, **kwargs)

    @classmethod
    def success(cls, output: Any = None, **kwargs) -> ToolResult:
        return cls(ok=True, output=output, **kwargs)
