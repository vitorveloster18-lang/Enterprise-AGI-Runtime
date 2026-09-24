import pytest

from egr.core.config import BudgetConfig, PricingConfig, ProviderConfig
from egr.core.errors import BudgetExceeded
from egr.models.gateway import CompletionRequest, Message, ModelGateway
from egr.models.providers.echo import EchoProvider


def _request(text: str = "objetivo de teste") -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content=text)], json_mode=False)


def _providers() -> list[EchoProvider]:
    cheap = EchoProvider(
        ProviderConfig(
            name="cheap",
            type="echo",
            external=True,
            priority=100,
            pricing=PricingConfig(input_per_1m=10.0, output_per_1m=30.0),
        )
    )
    local = EchoProvider(
        ProviderConfig(
            name="local",
            type="echo",
            external=False,
            priority=1,
            pricing=PricingConfig(),
        )
    )
    return [cheap, local]


def test_cost_is_estimated_from_pricing():
    provider = _providers()[0]
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    assert provider.estimate_cost(usage) == pytest.approx(40.0)
    assert provider.estimate_cost({}) == 0.0


def test_local_provider_has_zero_cost():
    local = _providers()[1]
    assert local.estimate_cost({"prompt_tokens": 10_000, "completion_tokens": 2_000}) == 0.0


def test_routing_strategies():
    cheap, local = _providers()

    by_priority = ModelGateway([cheap, local], routing="priority")
    assert by_priority.route("reasoning").name == "cheap"

    by_cost = ModelGateway([cheap, local], routing="cost")
    assert by_cost.route("reasoning").name == "local"

    local_first = ModelGateway([cheap, local], routing="local_first")
    assert local_first.route("reasoning").name == "local"


def test_local_first_still_reaches_external_when_needed():
    cheap, _ = _providers()
    gateway = ModelGateway([cheap], routing="local_first")
    assert gateway.route("reasoning").name == "cheap"


def test_usage_is_recorded_and_aggregated(runtime):
    runtime.gateway.providers[0].config.pricing = PricingConfig(input_per_1m=1.0, output_per_1m=2.0)
    runtime.gateway.complete(_request(), task_id="tsk-test", agent_id="runtime-agent")

    total = runtime.usage.totals()
    assert total["calls"] == 1
    assert total["total_cost"] > 0
    assert total["by_provider"][0]["provider"] == "echo"

    scoped = runtime.usage.totals(task_id="tsk-test")
    assert scoped["calls"] == 1
    assert runtime.usage.totals(task_id="outra-task")["calls"] == 0


def test_task_result_carries_cost_and_tokens(runtime):
    runtime.gateway.providers[0].config.pricing = PricingConfig(input_per_1m=1.0, output_per_1m=2.0)
    task = runtime.submit("Analisar documentos", agent_id="document-agent")

    assert task.result.model_calls == 1
    assert task.result.cost > 0
    assert set(task.result.tokens) == {"input", "output"}
    assert task.result.tokens["input"] > 0
    assert "spend" in runtime.status()


def test_budget_blocks_calls_and_records_event(runtime):
    runtime.gateway.providers[0].config.pricing = PricingConfig(input_per_1m=1.0, output_per_1m=2.0)
    runtime.gateway.budget = BudgetConfig(per_task=0.00001, on_exceeded="deny")

    first = runtime.gateway.complete(_request("primeira chamada"), task_id="tsk-budget")
    assert first.cost > 0

    with pytest.raises(BudgetExceeded):
        runtime.gateway.complete(_request("segunda chamada"), task_id="tsk-budget")

    events = runtime.audit.list(type="model.budget_blocked", limit=5)
    assert events
    assert events[0].payload["scope"] == "task"
    assert runtime.usage.totals(task_id="tsk-budget")["calls"] == 1


def test_budget_warn_does_not_block(runtime):
    runtime.gateway.providers[0].config.pricing = PricingConfig(input_per_1m=1.0, output_per_1m=2.0)
    runtime.gateway.budget = BudgetConfig(per_task=0.0000001, on_exceeded="warn")

    runtime.gateway.complete(_request(), task_id="tsk-warn")
    response = runtime.gateway.complete(_request(), task_id="tsk-warn")
    assert response.text
    assert runtime.usage.totals(task_id="tsk-warn")["calls"] == 2


def test_daily_budget_applies_across_tasks(runtime):
    runtime.gateway.providers[0].config.pricing = PricingConfig(input_per_1m=1.0, output_per_1m=2.0)
    runtime.gateway.budget = BudgetConfig(per_day=0.0001, on_exceeded="deny")

    runtime.gateway.complete(_request("a" * 400), task_id="tsk-1")
    with pytest.raises(BudgetExceeded):
        runtime.gateway.complete(_request("b" * 400), task_id="tsk-2")

    assert runtime.usage.totals_today()["calls"] == 1


def test_budget_exceeded_fails_the_task_with_governance_event(runtime):
    """Orçamento estourado: a task falha com motivo explícito, não degrada em silêncio."""

    runtime.gateway.providers[0].config.pricing = PricingConfig(input_per_1m=1.0, output_per_1m=2.0)
    runtime.gateway.budget = BudgetConfig(per_task=0.0, on_exceeded="deny")

    task = runtime.submit("Gerar relatório", agent_id="runtime-agent")

    assert task.status == "failed"
    assert "budget exceeded" in (task.error or "")
    assert runtime.audit.list(type="model.budget_blocked", limit=3)
    assert runtime.audit.list(type="task.failed", limit=3)
