"""Policy Engine: the Runtime decides, never the model.

    Agent -> Action Proposal -> Policy Engine -> ALLOW | DENY | REQUIRE_APPROVAL
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.errors import ConditionError
from ..domain.agent import AgentSpec
from ..domain.enums import DecisionType, Environment, RiskLevel
from ..domain.policy import Decision, Policy, PolicyRule
from ..domain.tool import ToolRequest
from .conditions import Condition
from .defaults import default_policies


@dataclass
class PolicyContext:
    """Everything the engine is allowed to know when deciding."""

    environment: Environment = Environment.DEVELOPMENT
    agent: AgentSpec | None = None
    enterprise_settings: dict = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW
    security: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


class PolicyEngine:
    def __init__(self, policies: list[Policy] | None = None):
        self.policies: list[Policy] = list(policies) if policies is not None else default_policies()
        self._conditions: dict[str, Condition] = {}

    # ---- management --------------------------------------------------
    def add_policy(self, policy: Policy, replace: bool = True) -> None:
        if replace:
            self.policies = [item for item in self.policies if item.id != policy.id]
        self.policies.append(policy)
        self.policies.sort(key=lambda item: (-item.priority, item.id))

    def set_policies(self, policies: list[Policy]) -> None:
        builtin = [policy for policy in self.policies if policy.builtin]
        merged = {policy.id: policy for policy in [*builtin, *policies]}
        self.policies = sorted(merged.values(), key=lambda item: (-item.priority, item.id))

    def list_policies(self) -> list[Policy]:
        return sorted(self.policies, key=lambda item: (-item.priority, item.id))

    # ---- decision ----------------------------------------------------
    def evaluate(self, request: ToolRequest, context: PolicyContext) -> Decision:
        if context.agent and not context.agent.allows_tool(request.tool):
            return Decision(
                decision=DecisionType.DENY,
                rule_id="agent-allowlist",
                policy_id=context.agent.id,
                reason=f"agent '{context.agent.id}' is not allowed to use tool '{request.tool}'",
            )

        evaluation = self._build_evaluation_context(request, context)

        for policy in self.list_policies():
            if not policy.enabled:
                continue
            for rule in policy.rules:
                if not self._matches_action(rule, request):
                    continue
                if rule.environments and context.environment not in rule.environments:
                    continue
                if rule.agents and (context.agent is None or context.agent.id not in rule.agents):
                    continue
                try:
                    if not self._condition(rule).evaluate(evaluation):
                        continue
                except ConditionError as exc:
                    # Fail closed: a broken rule never authorizes.
                    return Decision(
                        decision=DecisionType.DENY,
                        rule_id=rule.id,
                        policy_id=policy.id,
                        reason=f"condition error: {exc}",
                    )
                return Decision(
                    decision=rule.decision,
                    rule_id=rule.id,
                    policy_id=policy.id,
                    reason=rule.reason or f"matched rule '{rule.id}'",
                    required_role=rule.required_role,
                )

        return Decision(
            decision=DecisionType.DENY,
            rule_id=None,
            policy_id=None,
            reason="default deny: no policy rule matched this action",
        )

    # ---- helpers -----------------------------------------------------
    def _condition(self, rule: PolicyRule) -> Condition:
        key = f"{rule.id}:{rule.condition}"
        if key not in self._conditions:
            self._conditions[key] = Condition(rule.condition)
        return self._conditions[key]

    @staticmethod
    def _matches_action(rule: PolicyRule, request: ToolRequest) -> bool:
        pattern = (rule.action or "*").strip()
        action = request.policy_action
        if pattern in ("*", ""):
            return True
        if pattern == action:
            return True
        if pattern.endswith(".*") and action.startswith(pattern[:-1]):
            return True
        if pattern.startswith("*") and action.endswith(pattern[1:]):
            return True
        return False

    def _build_evaluation_context(
        self, request: ToolRequest, context: PolicyContext
    ) -> dict[str, Any]:
        evaluation: dict[str, Any] = {
            "tool": request.tool,
            "action": request.policy_action,
            "environment": str(context.environment),
            "agent": context.agent.id if context.agent else None,
            "risk": str(context.risk),
            "task_id": request.task_id,
            "external_ai": context.enterprise_settings.get("external_ai", "allowed"),
            "data_residency": context.enterprise_settings.get("data_residency", "local"),
        }
        evaluation.update(context.security)
        evaluation.update(context.extra)
        # request args are last so a policy can always see the payload, but
        # never overwrite reserved keys silently
        for key, value in (request.args or {}).items():
            if key not in ("environment", "agent", "risk", "tool", "action"):
                evaluation[key] = value
        evaluation["args"] = request.args or {}
        return evaluation

    def explain(self, request: ToolRequest, context: PolicyContext) -> dict:
        evaluation = self._build_evaluation_context(request, context)
        trace = []
        for policy in self.list_policies():
            for rule in policy.rules:
                matched_action = self._matches_action(rule, request)
                env_ok = (not rule.environments) or context.environment in rule.environments
                try:
                    condition_ok = self._condition(rule).evaluate(evaluation)
                    condition_error = None
                except ConditionError as exc:
                    condition_ok = False
                    condition_error = str(exc)
                trace.append(
                    {
                        "policy": policy.id,
                        "rule": rule.id,
                        "action": rule.action,
                        "condition": rule.condition or "true",
                        "decision": str(rule.decision),
                        "matched_action": matched_action,
                        "environment_ok": env_ok,
                        "condition_ok": condition_ok,
                        "condition_error": condition_error,
                    }
                )
        return {"decision": self.evaluate(request, context), "trace": trace}
