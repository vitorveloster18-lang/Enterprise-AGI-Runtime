"""Agent: a cognitive unit. Never bound to a single model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import Environment


class ModelSpec(BaseModel):
    """What the agent *needs*, not which vendor provides it."""

    capability: str = "reasoning"  # reasoning | fast | vision | embedding | code
    provider: str | None = None  # optional explicit pin
    model: str | None = None
    temperature: float = 0.2
    max_tokens: int = 2048
    allow_external: bool = True


class AgentPermissions(BaseModel):
    tools: list[str] = Field(default_factory=list)  # exact name or prefix wildcard: "filesystem.*"
    namespaces: list[str] = Field(default_factory=list)
    max_risk: str = "high"


class AgentSpec(BaseModel):
    id: str
    area: str | None = None  # área da empresa (finance, hr…); None = global
    name: str | None = None
    version: str = "1.0.0"
    objective: str = ""
    model: ModelSpec = Field(default_factory=ModelSpec)
    memory: list[str] = Field(default_factory=lambda: ["default"])
    permissions: AgentPermissions = Field(default_factory=AgentPermissions)
    environment: Environment = Environment.DEVELOPMENT
    system_prompt: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def allows_tool(self, tool_name: str) -> bool:
        """Agent-level allowlist. Empty list means 'inherit from policy engine'."""
        if not self.permissions.tools:
            return True
        for pattern in self.permissions.tools:
            if pattern == tool_name:
                return True
            if pattern.endswith(".*") and tool_name.startswith(pattern[:-1]):
                return True
            if pattern == "*":
                return True
        return False

    @property
    def risk_rank(self) -> int:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return order.get(self.permissions.max_risk, 2)
