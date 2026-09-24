"""Fase 8 — Evaluation: casos, métricas, limites, baseline, regressão e segurança."""

from __future__ import annotations

import pytest

from egr.core.config import EGRConfig, Settings
from egr.core.errors import ConfigError
from egr.domain.enums import EvaluationStatus, EvaluationTarget, EventType, FindingSeverity
from egr.domain.evaluation import EvaluationCase, EvaluationSuite, Thresholds
from egr.evaluation.metrics import aggregate, compare, percentile
from egr.evaluation.security import scan
from egr.evaluation.suites import sample_args, smoke_suite
from egr.runtime.runtime import Runtime

PRECO_TOOL = '''
"""Ferramenta: total de um item com desconto."""

from __future__ import annotations

from egr.domain.enums import RiskLevel
from egr.domain.tool import ToolRequest, ToolResult, ToolSpec
from egr.tools.protocol import Tool


class PrecoTool(Tool):
    spec = ToolSpec(
        name="venda.preco",
        description="calcula o total com desconto",
        parameters={
            "quantidade": {"type": "integer", "required": True},
            "unitario": {"type": "number", "required": True},
            "desconto": {"type": "number", "required": False},
        },
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx) -> ToolResult:
        quantidade = float(request.args["quantidade"])
        unitario = float(request.args["unitario"])
        desconto = float(request.args.get("desconto") or 0)
        return ToolResult.success({"total": round(quantidade * unitario * (1 - desconto / 100), 2)})
'''

BROKEN_TOOL = PRECO_TOOL.replace("(1 - desconto / 100)", "1")

NO_MEMORY_AGENT = """
id: sem-memoria
name: Agente sem memória declarada
version: 0.1.0
objective: executar leitura de arquivos
memory: []
permissions:
  tools: ["filesystem.read"]
  namespaces: ["default"]
  max_risk: low
environment: development
"""

WILDCARD_AGENT = """
# passa pela verificação da Fase 7 (tem objetivo) mas é arriscado:
# é isso que a varredura de segurança da Fase 8 existe para pegar.
id: god
name: Agente sem limites
version: 0.1.0
objective: executar qualquer coisa que for pedida
permissions:
  tools: ["*"]
  namespaces: []
  max_risk: critical
environment: development
"""


@pytest.fixture()
def runtime(workspace):
    instance = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    instance.sync_all()
    yield instance
    instance.close()


def _apply_tool(runtime, source: str, name: str = "venda.preco") -> None:
    proposal = runtime.workbench.propose("tool", name, source)
    runtime.workbench.trial(proposal.id, {"quantidade": 1, "unitario": 1})
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id)


def _suite(**overrides) -> EvaluationSuite:
    payload = {
        "id": "venda.preco",
        "name": "Suíte do cálculo de preço",
        "target_kind": EvaluationTarget.TOOL,
        "target": "venda.preco",
        "cases": [
            EvaluationCase(
                id="sem-desconto",
                args={"quantidade": 3, "unitario": 10},
                expect_ok=True,
                expect=["output['total'] == 30.0"],
            ),
            EvaluationCase(
                id="com-desconto",
                args={"quantidade": 2, "unitario": 50, "desconto": 10},
                expect_ok=True,
                expect=["output['total'] == 90.0"],
            ),
        ],
        "thresholds": Thresholds(min_pass_rate=1.0, max_regressions=0),
    }
    payload.update(overrides)
    return EvaluationSuite(**payload)


# ----------------------------------------------------------------------
# métricas
# ----------------------------------------------------------------------
def test_percentile_and_aggregate():
    from egr.domain.evaluation import CaseResult

    cases = [
        CaseResult(case_id="a", ok=True, duration_ms=100, cost=0.001),
        CaseResult(case_id="b", ok=True, duration_ms=200, cost=0.001),
        CaseResult(case_id="c", ok=False, duration_ms=300, cost=0.002),
    ]

    assert percentile([100, 200, 300], 95) == 300

    metrics = aggregate(cases)
    assert metrics["pass_rate"] == pytest.approx(2 / 3)
    assert metrics["total_cost"] == pytest.approx(0.004)
    assert metrics["avg_duration_ms"] == 200
    assert metrics["failed_cases"] == ["c"]


# ----------------------------------------------------------------------
# suíte derivada da declaração
# ----------------------------------------------------------------------
def test_sample_args_follows_declared_types():
    args = sample_args({"a": {"type": "integer"}, "b": {"type": "string"}, "c": {"type": "boolean"}})

    assert args == {"a": 1, "b": "teste", "c": True}


def test_smoke_suite_needs_a_registered_tool(runtime):
    with pytest.raises(ConfigError):
        smoke_suite(runtime, "tool", "nao.existe")


def test_smoke_suite_derives_cases_from_the_declaration(runtime):
    _apply_tool(runtime, PRECO_TOOL)

    suite = smoke_suite(runtime, "tool", "venda.preco")

    assert suite.id == "smoke-venda.preco"
    assert any(case.id == "chamada-valida" for case in suite.cases)
    assert any(case.id == "sem-argumentos" for case in suite.cases)  # há parâmetros obrigatórios


def test_policy_smoke_skips_conditional_and_shadowed_rules(runtime):
    suite = smoke_suite(runtime, "policy", "builtin-baseline")

    for case in suite.cases:
        assert "condition" not in case.args
    assert suite.metadata["fora_do_escopo"]  # lacunas declaradas, não escondidas


# ----------------------------------------------------------------------
# execução e veredito
# ----------------------------------------------------------------------
def test_suite_runs_and_passes(runtime):
    _apply_tool(runtime, PRECO_TOOL)
    runtime.evaluation_suites["venda.preco"] = _suite()

    run = runtime.evaluator.run(runtime.evaluation_suites["venda.preco"], actor="teste")

    assert run.status == EvaluationStatus.PASSED
    assert run.pass_rate == 1.0
    assert len(run.cases) == 2
    assert run.metrics["total_cost"] == 0.0


def test_assertion_failure_is_measured_not_guessed(runtime):
    _apply_tool(runtime, BROKEN_TOOL)
    runtime.evaluation_suites["venda.preco"] = _suite()

    run = runtime.evaluator.run(runtime.evaluation_suites["venda.preco"])

    assert run.status == EvaluationStatus.FAILED
    failed = [case.case_id for case in run.cases if not case.ok]
    assert failed == ["com-desconto"]
    assert any("taxa de acerto" in reason for reason in run.reasons)


def test_min_pass_rate_threshold_blocks_approval(runtime):
    _apply_tool(runtime, BROKEN_TOOL)
    suite = _suite(thresholds=Thresholds(min_pass_rate=0.5, max_regressions=0))
    runtime.evaluation_suites["venda.preco"] = suite

    run = runtime.evaluator.run(suite)

    assert run.status == EvaluationStatus.PASSED  # 50% satisfaz o mínimo declarado
    assert run.pass_rate == 0.5


def test_latency_threshold_is_enforced(runtime):
    _apply_tool(runtime, PRECO_TOOL)
    suite = _suite(thresholds=Thresholds(min_pass_rate=1.0, max_p95_duration_ms=1))
    runtime.evaluation_suites["venda.preco"] = suite

    run = runtime.evaluator.run(suite)

    assert run.status == EvaluationStatus.FAILED
    assert any("p95" in reason for reason in run.reasons)


def test_empty_suite_is_refused(runtime):
    with pytest.raises(ConfigError):
        runtime.evaluator.run(_suite(cases=[]))


# ----------------------------------------------------------------------
# baseline e regressão
# ----------------------------------------------------------------------
def test_second_run_detects_the_regression(runtime):
    _apply_tool(runtime, PRECO_TOOL)
    suite = _suite()
    runtime.evaluation_suites["venda.preco"] = suite

    first = runtime.evaluator.run(suite)
    assert first.status == EvaluationStatus.PASSED

    _apply_tool(runtime, BROKEN_TOOL)
    second = runtime.evaluator.run(suite)

    assert second.status == EvaluationStatus.REGRESSED
    assert second.baseline_run == first.id
    assert second.comparison is not None
    assert second.comparison.new_failures == ["com-desconto"]
    assert second.comparison.pass_rate_delta < 0


def test_explicit_baseline_can_be_chosen(runtime):
    _apply_tool(runtime, PRECO_TOOL)
    suite = _suite()
    runtime.evaluation_suites["venda.preco"] = suite

    first = runtime.evaluator.run(suite)
    runtime.evaluator.run(suite, baseline=first.id)

    assert runtime.evaluations.get(first.id) is not None
    with pytest.raises(ConfigError, match="baseline"):
        runtime.evaluator.run(suite, baseline="eva_nao_existe")


def test_compare_reports_latency_drift(runtime):
    _apply_tool(runtime, PRECO_TOOL)
    suite = _suite()
    runtime.evaluation_suites["venda.preco"] = suite

    baseline = runtime.evaluator.run(suite)
    current = runtime.evaluator.run(suite)
    current.metrics["avg_duration_ms"] = int(baseline.metrics["avg_duration_ms"] * 3) or 300

    report = compare(current, baseline, Thresholds(max_latency_drift_pct=10))

    assert report.regressions >= 1
    assert any("latência" in note for note in report.notes)


def test_runs_are_persisted_and_listed(runtime):
    _apply_tool(runtime, PRECO_TOOL)
    suite = _suite()
    runtime.evaluation_suites["venda.preco"] = suite

    run = runtime.evaluator.run(suite)

    assert runtime.evaluations.get(run.id) is not None
    assert [item.id for item in runtime.evaluations.list(suite_id=suite.id)] == [run.id]
    assert runtime.evaluations.stats().get("passed") == 1


# ----------------------------------------------------------------------
# outros alvos
# ----------------------------------------------------------------------
def test_workflow_evaluation_runs_a_real_run(runtime):
    suite = smoke_suite(runtime, "workflow", "invoice-processing")
    runtime.evaluation_suites[suite.id] = suite

    run = runtime.evaluator.run(suite)

    assert run.status == EvaluationStatus.PASSED
    case = run.cases[0]
    assert case.metadata["status"] in ("completed", "partial")


def test_policy_evaluation_simulates_decisions(runtime):
    suite = smoke_suite(runtime, "policy", "builtin-baseline")
    runtime.evaluation_suites[suite.id] = suite

    run = runtime.evaluator.run(suite)

    assert run.status == EvaluationStatus.PASSED
    assert run.cases[0].metadata["decision"]


def test_agent_evaluation_runs_a_task(runtime):
    suite = smoke_suite(runtime, "agent", "document-agent")
    runtime.evaluation_suites[suite.id] = suite

    run = runtime.evaluator.run(suite)

    assert run.cases
    assert run.cases[0].metadata["status"]
    assert "answer" in (run.cases[0].output or {})


def test_missing_target_is_an_error_not_a_failure(runtime):
    suite = EvaluationSuite(
        id="fantasma",
        target_kind=EvaluationTarget.TOOL,
        target="nao.existe",
        cases=[EvaluationCase(id="c1")],
    )

    run = runtime.evaluator.run(suite)

    assert run.status == EvaluationStatus.ERROR
    assert "não foi possível avaliar" in run.reasons[0]


# ----------------------------------------------------------------------
# segurança
# ----------------------------------------------------------------------
def test_security_scan_flags_wildcard_agent(runtime):
    proposal = runtime.workbench.propose("agent", "god", WILDCARD_AGENT)
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id)

    findings = scan(runtime, "agent", "god")

    codes = {finding.code for finding in findings}
    assert "curinga" in codes
    assert any(finding.severity == FindingSeverity.CRITICAL for finding in findings)


def test_security_scan_flags_agent_without_memory(runtime):
    proposal = runtime.workbench.propose("agent", "sem-memoria", NO_MEMORY_AGENT)
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id)

    codes = {finding.code for finding in scan(runtime, "agent", "sem-memoria")}
    assert "sem-memoria" in codes


def test_security_scan_of_a_sane_artifact_is_clean(runtime):
    assert scan(runtime, "agent", "document-agent") == []


def test_security_finding_blocks_the_verdict(runtime):
    proposal = runtime.workbench.propose("agent", "god", WILDCARD_AGENT)
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id)

    suite = EvaluationSuite(
        id="god",
        target_kind=EvaluationTarget.AGENT,
        target="god",
        cases=[EvaluationCase(id="c1", args={"objective": "faça algo"}, expect=["status != 'failed'"])],
    )
    runtime.evaluation_suites["god"] = suite

    run = runtime.evaluator.run(suite)

    assert run.blocking_findings
    assert run.status == EvaluationStatus.FAILED
    assert any("segurança" in reason for reason in run.reasons)


# ----------------------------------------------------------------------
# auditoria e status
# ----------------------------------------------------------------------
def test_evaluation_is_audited(runtime):
    _apply_tool(runtime, PRECO_TOOL)
    suite = _suite()
    runtime.evaluation_suites["venda.preco"] = suite

    runtime.evaluator.run(suite)

    types = [str(event.type) for event in runtime.audit.list(limit=50)]
    assert EventType.EVAL_RUN_STARTED in types
    assert EventType.EVAL_RUN_FINISHED in types


def test_evaluation_status_summarises_the_environment(runtime):
    _apply_tool(runtime, PRECO_TOOL)
    suite = _suite()
    runtime.evaluation_suites["venda.preco"] = suite
    runtime.evaluator.run(suite)

    data = runtime.evaluation_status()

    assert data["suites"]["total"] >= 1
    assert data["runs"]["total"] >= 1
    assert "passed" in data["runs"]["by_status"]
    assert data["runs"]["last_by_suite"]["venda.preco"]["status"] == "passed"


def test_status_command_includes_evaluation(runtime):
    assert "evaluation" in runtime.status()
