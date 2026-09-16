import pytest

from egr.domain.enums import DecisionType
from egr.domain.policy import Policy, PolicyRule
from egr.policies.conditions import ConditionError, evaluate


def decide(runtime, tool, environment="development", args=None, agent_id=None):
    _, decision = runtime.request_action(tool, args or {}, environment=environment, agent_id=agent_id)
    return decision


def test_read_is_allowed_in_all_environments(runtime):
    for environment in ("development", "staging", "production"):
        assert decide(runtime, "filesystem.read", environment).allowed


def test_python_exec_needs_approval_outside_development(runtime):
    assert decide(runtime, "python.execute", "development").allowed
    assert decide(runtime, "python.execute", "staging").needs_approval
    assert decide(runtime, "python.execute", "production").needs_approval


def test_unknown_action_is_denied_by_default(runtime):
    decision = decide(runtime, "erp.create_invoice", "development")
    assert decision.decision == DecisionType.DENY
    assert "default deny" in decision.reason


def test_agent_allowlist_blocks_tools(runtime):
    decision = decide(runtime, "python.execute", "development", agent_id="finance-agent")
    assert decision.decision == DecisionType.DENY
    assert "not allowed" in decision.reason


def test_threshold_rule_uses_request_arguments(runtime):
    runtime.policy.add_policy(
        Policy(
            id="finance-payment",
            priority=50,
            rules=[
                PolicyRule(id="auto", action="payment.create", condition="amount < 5000", decision=DecisionType.ALLOW),
                PolicyRule(
                    id="approval",
                    action="payment.create",
                    condition="amount >= 5000",
                    decision=DecisionType.REQUIRE_APPROVAL,
                    required_role="finance_manager",
                ),
            ],
        )
    )
    assert decide(runtime, "payment.create", args={"amount": 3000}).allowed
    approval = decide(runtime, "payment.create", args={"amount": 9000})
    assert approval.needs_approval
    assert approval.required_role == "finance_manager"


def test_broken_condition_fails_closed(runtime):
    runtime.policy.add_policy(
        Policy(
            id="broken",
            priority=99,
            rules=[
                PolicyRule(id="bad", action="filesystem.read", condition="__import__('os').system('ls')"),
            ],
        )
    )
    decision = decide(runtime, "filesystem.read")
    assert decision.decision == DecisionType.DENY
    assert "condition error" in decision.reason


def test_condition_evaluator_is_safe():
    assert evaluate("amount >= 5000", {"amount": 6000}) is True
    assert evaluate("amount >= 5000", {"amount": 100}) is False
    assert evaluate("amount >= 5000", {}) is False  # missing data never authorizes
    assert evaluate("environment == 'production' and risk in ['high', 'critical']",
                    {"environment": "production", "risk": "high"}) is True
    assert evaluate("lower(name) == 'acme'", {"name": "ACME"}) is True
    with pytest.raises(ConditionError):
        evaluate("open('/etc/passwd').read()", {})
    with pytest.raises(ConditionError):
        evaluate("(1).__class__.__bases__", {})
