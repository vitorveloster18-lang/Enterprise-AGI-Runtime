"""Lacuna 8b — laboratório de avaliação: a resposta presta? e aguenta carga?

A Fase 8 mede o encanamento (passa? custa? demora?). O laboratório mede o que
faltava: **qualidade** (a resposta serve para o que foi pedido) e **carga**
(quantas requisições o Runtime aguenta sem quebrar o governo — orçamento
incluído, e contado à parte).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from egr.api.server import create_app
from egr.core.config import EGRConfig, Settings
from egr.domain.enums import Environment, EvaluationTarget, EventType
from egr.domain.evaluation import EvaluationCase, EvaluationSuite, LoadRun, Thresholds
from egr.evaluation.quality import aggregate, judge_case, similarity, tokens
from egr.runtime.runtime import Runtime
from egr.templates import render_workspace


# ----------------------------------------------------------------------
# apoio
# ----------------------------------------------------------------------
def make_runtime(tmp_path: Path) -> Runtime:
    render_workspace(tmp_path, sample=False)
    return Runtime(Settings(workspace=tmp_path, config=EGRConfig()), enable_logging=False)


def suite(
    *, min_quality: float | None = None, cases: int = 1, expected: str = "documentos do workspace"
) -> EvaluationSuite:
    return EvaluationSuite(
        id="lab",
        name="Laboratório",
        target_kind=EvaluationTarget.AGENT,
        target="document-agent",
        environment=Environment.DEVELOPMENT,
        thresholds=Thresholds(min_pass_rate=0.0, min_quality=min_quality),
        cases=[
            EvaluationCase(
                id=f"c{index}",
                args={"objective": "liste os documentos do workspace"},
                expected=expected,
                expected_contains=["documento"],
            )
            for index in range(cases)
        ],
    )


class FakeResponse:
    def __init__(self, text: str = "", cost: float = 0.0):
        self.text = text
        self.provider = "juiz"
        self.model = "juiz-1"
        self.cost = cost
        self.usage: dict = {}
        self.latency_ms = 1
        self.external = False
        self.raw: dict | None = None


# ----------------------------------------------------------------------
# 1. qualidade determinística
# ----------------------------------------------------------------------
def test_tokens_drop_stopwords_and_punctuation():
    assert tokens("A nota fiscal, o relatório!") == {"nota", "fiscal", "relatório"}


def test_similarity_rewards_overlap_and_required_terms():
    high, reason = similarity("a nota fiscal e o relatório mensal", "nota fiscal relatório", required=["nota"])
    low, _ = similarity("nada a ver aqui", "nota fiscal relatório", required=["nota"])

    assert high > low
    assert "sobreposição" in reason


def test_similarity_without_expected_uses_only_required_terms():
    score, _ = similarity("o relatório cita a nota", "", required=["nota"])
    zero, reason = similarity("o relatório cita a nota", "", required=["ausente"])

    assert score == 1.0
    assert zero == 0.0
    assert "trechos" in reason


def test_judge_case_similarity_method():
    case = EvaluationCase(id="c", expected="nota fiscal", expected_contains=["nota"])

    score = judge_case(None, case, "a nota fiscal está anexa", method="similaridade")

    assert score.method == "similaridade"
    assert 0.0 < score.score <= 1.0
    assert not score.degraded


# ----------------------------------------------------------------------
# 2. juiz de modelo (e a degradação declarada)
# ----------------------------------------------------------------------
def test_model_judge_reads_note_and_reason(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.gateway.complete = lambda request, **kwargs: FakeResponse(  # type: ignore[method-assign]
        "NOTA: 0.8 | MOTIVO: resposta cobre o essencial", cost=0.002
    )

    score = judge_case(runtime, EvaluationCase(id="c", expected="o total é 10"), "o total é 10", method="modelo")

    assert score.method == "modelo"
    assert score.score == 0.8
    assert "cobre o essencial" in score.reason
    assert score.cost == 0.002
    runtime.close()


def test_failed_judge_falls_back_and_says_so(tmp_path):
    runtime = make_runtime(tmp_path)

    def broken(request, **kwargs):
        raise RuntimeError("provedor fora do ar")

    runtime.gateway.complete = broken  # type: ignore[method-assign]
    case = EvaluationCase(id="c", expected="nota fiscal", expected_contains=["nota"])

    score = judge_case(runtime, case, "a nota fiscal", method="modelo")

    assert score.degraded is True
    assert score.method == "similaridade"
    assert "indisponível" in score.reason
    runtime.close()


def test_judge_out_of_format_is_degradation_not_crash(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.gateway.complete = lambda request, **kwargs: FakeResponse("não entendi")  # type: ignore[method-assign]

    score = judge_case(runtime, EvaluationCase(id="c", expected="x"), "y", method="modelo")

    assert score.degraded and score.method == "similaridade"
    runtime.close()


def test_aggregate_reports_method_and_degradation():
    scores = [
        judge_case(None, EvaluationCase(id="a", expected="x"), "x", method="similaridade"),
        judge_case(None, EvaluationCase(id="b", expected="x"), "nada", method="similaridade"),
    ]

    metrics = aggregate(scores)

    assert metrics["casos"] == 2
    assert metrics["mínima"] == 0.0
    assert metrics["método"] == "similaridade"
    assert metrics["degradados"] == 0


def test_aggregate_without_scores():
    assert aggregate([])["casos"] == 0


# ----------------------------------------------------------------------
# 3. rodada com julgamento
# ----------------------------------------------------------------------
def test_judge_run_scores_and_audits(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.evaluator.judge(suite(), method="similaridade", actor="human:vitor")

    assert EventType.EVAL_JUDGED in [event.type for event in runtime.audit.list(limit=30)]
    runtime.close()


def test_judge_below_threshold_reproves_the_run(tmp_path):
    runtime = make_runtime(tmp_path)
    report = runtime.evaluator.judge(suite(min_quality=0.9), method="similaridade")

    assert report["reprovado"] == "sim"
    assert report["métricas"]["qualidade_média"] < 0.9
    run = runtime.evaluations.get(report["run"])
    assert any("qualidade média" in reason for reason in run.reasons)
    runtime.close()


def test_judge_without_expectation_scores_nothing(tmp_path):
    runtime = make_runtime(tmp_path)
    plain = EvaluationSuite(
        id="sem-esperado",
        target_kind=EvaluationTarget.AGENT,
        target="document-agent",
        thresholds=Thresholds(min_pass_rate=0.0),
        cases=[EvaluationCase(id="c", args={"objective": "liste os documentos"})],
    )

    report = runtime.evaluator.judge(plain, method="similaridade")

    assert report["métricas"]["casos"] == 0
    assert report["reprovado"] == "não"
    runtime.close()


# ----------------------------------------------------------------------
# 4. comparação de provedores
# ----------------------------------------------------------------------
def test_compare_unknown_provider_is_reported_not_executed(tmp_path):
    runtime = make_runtime(tmp_path)

    report = runtime.evaluator.compare(suite(), ["echo", "nao-existe"])

    rows = {row["provedor"]: row for row in report["provedores"]}
    assert rows["echo"]["executou"] is True
    assert rows["nao-existe"]["executou"] is False
    assert "não configurado" in rows["nao-existe"]["erro"]
    assert report["melhor"] == "echo"
    assert EventType.EVAL_COMPARED in [event.type for event in runtime.audit.list(limit=30)]
    runtime.close()


def test_compare_restores_the_agent_provider(tmp_path):
    """Fixar provedor para o teste não pode vazar para o agente."""

    runtime = make_runtime(tmp_path)
    runtime.agents["document-agent"].model.provider = "original"

    runtime.evaluator.compare(suite(), ["echo"])

    assert runtime.agents["document-agent"].model.provider == "original"
    runtime.close()


def test_compare_says_when_the_suite_does_not_use_a_model(tmp_path):
    runtime = make_runtime(tmp_path)
    tool_suite = EvaluationSuite(
        id="tool",
        target_kind=EvaluationTarget.TOOL,
        target="filesystem.read",
        thresholds=Thresholds(min_pass_rate=0.0),
        cases=[EvaluationCase(id="c", args={"path": "egr.yaml"}, expected="egr", expected_contains=["egr"])],
    )

    report = runtime.evaluator.compare(tool_suite, ["echo"])

    assert report["usa_modelo"] is False
    assert any("não usa modelo" in note for note in report["observações"])
    runtime.close()


def test_provider_pin_is_honoured_by_the_agent_engine(tmp_path):
    runtime = make_runtime(tmp_path)
    seen: list[str] = []

    def fake(request, **kwargs):
        seen.append(kwargs.get("provider"))
        return FakeResponse('{"objetivo": "x", "passos": []}')

    runtime.gateway.complete = fake  # type: ignore[method-assign]
    runtime.agents["document-agent"].model.provider = "echo"

    runtime.evaluator.compare(suite(), ["echo"])

    assert "echo" in seen
    runtime.close()


# ----------------------------------------------------------------------
# 5. carga
# ----------------------------------------------------------------------
def test_load_runs_every_request_and_measures(tmp_path):
    runtime = make_runtime(tmp_path)

    load = runtime.evaluator.load(suite(), requests=4, concurrency=2)

    assert load.requests == 4
    assert load.metrics["requests"] == 4
    assert load.metrics["p95_duration_ms"] >= 0
    assert load.metrics["requests_per_second"] > 0
    assert load.status in ("passed", "degraded", "failed")
    runtime.close()


def test_load_is_persisted_and_audited(tmp_path):
    runtime = make_runtime(tmp_path)
    load = runtime.evaluator.load(suite(), requests=2, concurrency=1)

    assert runtime.evaluation_loads.get(load.id) is not None
    assert runtime.evaluation_loads.count() == 1
    assert EventType.EVAL_LOAD_FINISHED in [event.type for event in runtime.audit.list(limit=30)]
    runtime.close()


def test_load_counts_budget_denials_separately(tmp_path):
    """Estourar orçamento não é lentidão: é governo funcionando."""

    runtime = make_runtime(tmp_path)

    class Denied:
        ok = False
        cost = 0.0
        error = "orçamento diário excedido (budget exceeded)"
        output = None
        duration_ms = 1
        checks: ClassVar[list] = []

    runtime.evaluator._run_case = lambda *args, **kwargs: Denied()  # type: ignore[method-assign]
    load = runtime.evaluator.load(suite(), requests=3, concurrency=1)

    assert load.metrics["budget_denials"] == 3
    assert load.metrics["errors"] == 3
    assert any("orçamento" in reason for reason in load.reasons)
    runtime.close()


def test_load_status_degrades_on_errors_without_denials(tmp_path):
    runtime = make_runtime(tmp_path)

    class Broken:
        ok = False
        cost = 0.0
        error = "ferramenta quebrou"
        output = None
        duration_ms = 1
        checks: ClassVar[list] = []

    runtime.evaluator._run_case = lambda *args, **kwargs: Broken()  # type: ignore[method-assign]
    load = runtime.evaluator.load(suite(), requests=4, concurrency=2)

    assert load.error_rate == 1.0
    assert load.status in ("degraded", "failed")
    assert any("taxa de erro" in reason for reason in load.reasons)
    runtime.close()


def test_load_report_summary(tmp_path):
    runtime = make_runtime(tmp_path)
    load = runtime.evaluator.load(suite(), requests=2, concurrency=1)

    summary = load.summary()

    assert summary["requisições"] == 2
    assert "p95_ms" in summary
    assert summary["situação"] == load.status
    runtime.close()


# ----------------------------------------------------------------------
# 6. CLI e API
# ----------------------------------------------------------------------
def test_cli_judge_compare_and_load(tmp_path):
    from egr.cli.commands.evaluation import app as eval_app

    runtime = make_runtime(tmp_path)
    runtime.suites.save(suite())
    runtime.close()
    runner = CliRunner()

    judged = runner.invoke(eval_app, ["judge", "lab", "--method", "similaridade", "-w", str(tmp_path)])
    compared = runner.invoke(eval_app, ["compare", "lab", "--models", "echo,nao-existe", "-w", str(tmp_path)])
    loaded = runner.invoke(eval_app, ["load", "lab", "-n", "3", "-c", "2", "-w", str(tmp_path)])
    history = runner.invoke(eval_app, ["loads", "-w", str(tmp_path)])

    assert judged.exit_code == 0, judged.output
    assert "qualidade média" in judged.output
    assert compared.exit_code == 0, compared.output
    assert "Comparação" in compared.output
    assert loaded.exit_code == 0, loaded.output
    assert "por segundo" in loaded.output
    assert history.exit_code == 0 and "Cargas" in history.output


def test_cli_judge_fails_when_quality_is_below_the_limit(tmp_path):
    from egr.cli.commands.evaluation import app as eval_app

    runtime = make_runtime(tmp_path)
    runtime.suites.save(suite(min_quality=0.95))
    runtime.close()

    result = CliRunner().invoke(
        eval_app, ["judge", "lab", "--method", "similaridade", "-w", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "abaixo do mínimo" in result.output


def test_api_lab_routes(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.suites.save(suite())
    client = TestClient(create_app(runtime))

    judged = client.post("/v1/eval/suites/lab/judge", json={"method": "similaridade", "by": "human:api"})
    compared = client.post("/v1/eval/suites/lab/compare", json={"models": ["echo", "nao-existe"]})
    loaded = client.post("/v1/eval/suites/lab/load", json={"requests": 3, "concurrency": 2})
    history = client.get("/v1/eval/loads")

    assert judged.status_code == 200 and "métricas" in judged.json()
    assert compared.status_code == 200 and compared.json()["melhor"] == "echo"
    assert loaded.status_code == 200 and loaded.json()["metrics"]["requests"] == 3
    assert history.status_code == 200 and len(history.json()) == 1
    assert client.post("/v1/eval/suites/inexistente/load", json={}).status_code == 404
    runtime.close()


def test_status_reports_load_history(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.evaluator.load(suite(), requests=2, concurrency=1)

    status = runtime.evaluation_status()

    assert status["cargas"]["total"] == 1
    assert status["cargas"]["recentes"][0]["suíte"] == "lab"
    runtime.close()


# ----------------------------------------------------------------------
# 7. coerência
# ----------------------------------------------------------------------
def test_load_run_model_summary_is_serializable(tmp_path):
    from datetime import datetime

    load = LoadRun(id="load_1", suite_id="lab", requests=2, concurrency=1, created_at=datetime.now())

    assert json.dumps(load.summary(), ensure_ascii=False)


def test_percentile_edges():
    from egr.evaluation.metrics import percentile

    assert percentile([], 95) == 0
    assert percentile([5, 1, 3], 50) == 3
    assert percentile([5, 1, 3], 100) == 5


@pytest.mark.parametrize("method", ["auto", "similaridade", "modelo"])
def test_every_method_returns_a_score(tmp_path, method):
    runtime = make_runtime(tmp_path)

    def broken(request, **kwargs):
        raise RuntimeError("sem provedor")

    runtime.gateway.complete = broken  # type: ignore[method-assign]
    score = judge_case(
        runtime,
        EvaluationCase(id="c", expected="nota", expected_contains=["nota"]),
        "a nota",
        method=method,
    )

    assert 0.0 <= score.score <= 1.0
    runtime.close()


# ----------------------------------------------------------------------
# 8. a carga não pode quebrar a trilha
# ----------------------------------------------------------------------
def test_load_keeps_the_audit_chain_intact(tmp_path):
    """Threads escrevendo juntas não podem quebrar o hash encadeado."""

    runtime = make_runtime(tmp_path)
    runtime.evaluator.load(suite(), requests=8, concurrency=4)

    assert runtime.audit.verify().get("valid", True) is not False


def test_audit_record_is_atomic_under_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from egr.domain.enums import EventType

    runtime = make_runtime(tmp_path)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda index: runtime.audit.record(EventType.EVAL_LOAD_FINISHED, actor=f"w{index}"), range(30)))

    broken = [item for item in runtime.audit.list(limit=200) if item.seq]
    assert len(broken) >= 30
    assert runtime.audit.verify()["valid"] is True
    runtime.close()
