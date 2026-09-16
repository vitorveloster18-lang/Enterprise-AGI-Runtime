"""Policy: authorization lives in the Runtime, never in the prompt."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import DecisionType, Environment


class PolicyRule(BaseModel):
    id: str
    action: str = "*"  # tool name or semantic action; supports "prefix.*"
    condition: str | None = None  # safe expression, e.g. "amount >= 5000"
    decision: DecisionType = DecisionType.ALLOW
    reason: str = ""
    environments: list[Environment] = Field(default_factory=list)  # empty = all
    required_role: str | None = None
    agents: list[str] = Field(default_factory=list)  # empty = all


class Policy(BaseModel):
    id: str
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    enabled: bool = True
    priority: int = 0  # higher wins
    rules: list[PolicyRule] = Field(default_factory=list)
    builtin: bool = False


class Decision(BaseModel):
    decision: DecisionType
    rule_id: str | None = None
    policy_id: str | None = None
    reason: str = ""
    required_role: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == DecisionType.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision == DecisionType.REQUIRE_APPROVAL
