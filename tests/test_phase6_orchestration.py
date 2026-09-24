"""Fase 6 — Orchestration: DAG, retry, condição, compensação, retomada e agenda."""

from __future__ import annotations

from datetime import datetime

import pytest
import yaml

from egr.core.config import EGRConfig, Settings
from egr.core.errors import ConfigError
from egr.core.timeutil import utcnow
from egr.domain.enums import RunStatus, StepRunStatus, TaskStatus
from egr.domain.workflow import Workflow, WorkflowStep
from egr.orchestration.cron import CronError, CronExpression
from egr.runtime.runtime import Runtime
from egr.runtime.triggers import matches


@pytest.fixture()
def runtime(workspace):
    instance = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    yield instance
    instance.close()


def _write_workflow(workspace, payload: dict) -> None:
    directory = workspace / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{payload['id']}.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _dag() -> dict:
    return {
        "id": "dag",
        "name": "DAG de teste",
        "steps": [
            {"id": "a", "objective": "passo A"},
            {"id": "b", "objective": "passo B", "depends_on": ["a"]},
            {"id": "c", "objective": "passo C", "depends_on": ["a"]},
            {"id": "d", "objective": "passo D", "depends_on": ["b", "c"]},
        ],
    }


# ----------------------------------------------------------------------
# DAG
# ----------------------------------------------------------------------
def test_validate_rejects_cycles_and_unknown_dependencies(runtime):
    workflow = Workflow(
        id="ciclo",
        steps=[
            WorkflowStep(id="x", objective="x", depends_on=["y"]),
            WorkflowStep(id="y", objective="y", depends_on=["x"]),
        ],
    )

    problems = runtime.orchestrator.validate(workflow)

    assert any("ciclo" in item for item in problems)

    orphan = Workflow(id="orfao", steps=[WorkflowStep(id="x", objective="x", depends_on=["nao-existe"])])
    assert any("não existe" in item for item in runtime.orchestrator.validate(orphan))


def test_validate_flags_broken_step_declarations(runtime):
    workflow = Workflow(
        id="quebrado",
        steps=[
            WorkflowStep(id="vazio"),
            WorkflowStep(id="ruim", objective="x", on_error="compensate"),
            WorkflowStep(id="invalido", objective="x", on_error="explodir"),
        ],
    )

    problems = runtime.orchestrator.validate(workflow)

    assert any("sem 'objective'" in item for item in problems)
    assert any("compensate_with" in item for item in problems)
    assert any("on_error inválido" in item for item in problems)


def test_levels_respect_dependencies(runtime, workspace):
    _write_workflow(workspace, _dag())
    runtime._load_workflows()

    levels = [[step.id for step in level] for level in runtime.orchestrator.levels(runtime.workflows["dag"])]

    assert levels == [["a"], ["b", "c"], ["d"]]


def test_run_executes_in_dependency_order_and_persists(runtime, workspace):
    _write_workflow(workspace, _dag())
    runtime._load_workflows()

    run = runtime.orchestrator.start("dag", created_by="vitor")

    assert run.status == RunStatus.COMPLETED
    assert [step.id for step in run.steps] == ["a", "b", "c", "d"]
    assert all(step.status == StepRunStatus.COMPLETED for step in run.steps)
    stored = runtime.runs.get(run.id)
    assert stored is not None and stored.status == RunStatus.COMPLETED
    assert runtime.runs.stats().get("completed") == 1


def test_step_output_is_available_to_the_next_step(runtime, workspace):
    payload = _dag()
    payload["steps"][1]["objective"] = "usar {{steps.a.answer}}"
    payload["steps"][0]["outputs"] = {"eco": "{{task.answer}}"}
    _write_workflow(workspace, payload)
    runtime._load_workflows()

    run = runtime.orchestrator.start("dag")

    assert run.context["a"]["outputs"]["eco"] == run.context["a"]["answer"]
    # {{steps.a.answer}} foi interpolado no objetivo do passo seguinte
    objective_b = runtime.tasks.get(run.step("b").task_id).objective
    assert objective_b == f"usar {run.context['a']['answer']}"


def test_parallel_levels_run_independently(runtime, workspace):
    payload = _dag()
    payload["parallel"] = True
    _write_workflow(workspace, payload)
    runtime._load_workflows()

    run = runtime.orchestrator.start("dag")

    assert run.status == RunStatus.COMPLETED
    assert run.step("b").task_id != run.step("c").task_id


# ----------------------------------------------------------------------
# falha, retry, tolerância e compensação
# ----------------------------------------------------------------------
def _flaky(runtime, monkeypatch, *, fail_times: int, status=TaskStatus.FAILED, error="boom"):
    """Faz o agente falhar as primeiras `fail_times` execuções."""

    original = runtime.agent_engine.run
    calls: list[str] = []

    def run(task):
        calls.append(task.id)
        if len(calls) <= fail_times:
            task.status = status
            task.error = error
            task.result = task.result or runtime.new_result()
            runtime.tasks.save(task)
            return task
        return original(task)

    monkeypatch.setattr(runtime.agent_engine, "run", run)
    return calls


def test_retry_retries_up_to_max_attempts(runtime, workspace, monkeypatch):
    payload = _dag()
    payload["steps"][0]["max_attempts"] = 2
    _write_workflow(workspace, payload)
    runtime._load_workflows()
    _flaky(runtime, monkeypatch, fail_times=1)

    run = runtime.orchestrator.start("dag")

    assert run.step("a").attempts == 2
    assert run.step("a").status == StepRunStatus.COMPLETED
    assert run.status == RunStatus.COMPLETED


def test_failure_without_tolerance_stops_the_run_and_skips_descendants(runtime, workspace, monkeypatch):
    _write_workflow(workspace, _dag())
    runtime._load_workflows()
    _flaky(runtime, monkeypatch, fail_times=99)

    run = runtime.orchestrator.start("dag")

    assert run.status == RunStatus.FAILED
    assert run.step("a").status == StepRunStatus.FAILED
    # run abortado: o passo a jusante nunca chegou a ser avaliado (não é skip)
    assert run.step("d").status == StepRunStatus.PENDING
    assert "sem tolerância" in (run.error or "")


def test_continue_policy_tolerates_failure_and_marks_run_partial(runtime, workspace, monkeypatch):
    payload = _dag()
    payload["on_error"] = "continue"
    _write_workflow(workspace, payload)
    runtime._load_workflows()
    _flaky(runtime, monkeypatch, fail_times=99)

    run = runtime.orchestrator.start("dag")

    assert run.status == RunStatus.PARTIAL
    assert run.summary["failed"] == 1
    assert run.summary["skipped"] == 3


def test_compensation_runs_the_declared_step(runtime, workspace, monkeypatch):
    payload = _dag()
    payload["steps"][0]["on_error"] = "compensate"
    payload["steps"][0]["compensate_with"] = "desfazer"
    payload["steps"].append({"id": "desfazer", "objective": "desfazer efeitos do passo A"})
    _write_workflow(workspace, payload)
    runtime._load_workflows()

    original = runtime.agent_engine.run
    calls: list[str] = []

    def run(task):
        calls.append(task.step_id)
        if task.step_id == "a":
            task.status = TaskStatus.FAILED
            task.error = "boom"
            task.result = task.result or runtime.new_result()
            runtime.tasks.save(task)
            return task
        return original(task)

    monkeypatch.setattr(runtime.agent_engine, "run", run)

    run = runtime.orchestrator.start("dag")

    assert "desfazer" in calls
    assert run.step("desfazer").status == StepRunStatus.COMPLETED
    compensation = [
        event
        for event in runtime.audit.list(limit=200)
        if event.payload.get("action") == "workflow_compensation"
    ]
    assert compensation and compensation[-1].payload["compensation_step"] == "desfazer"


def test_condition_false_skips_only_that_step(runtime, workspace):
    payload = _dag()
    payload["inputs"] = {"executar_b": False}
    payload["steps"][1]["condition"] = "inputs['executar_b']"
    _write_workflow(workspace, payload)
    runtime._load_workflows()

    run = runtime.orchestrator.start("dag")

    assert run.step("b").status == StepRunStatus.SKIPPED
    assert run.step("c").status == StepRunStatus.COMPLETED
    # 'd' depende de 'b' (pulado) e 'c': dependência pulada propaga o skip
    assert run.step("d").status == StepRunStatus.SKIPPED


# ----------------------------------------------------------------------
# aprovação pausa e retomada
# ----------------------------------------------------------------------
def _approval_blocker(runtime, monkeypatch, *, times: int = 1):
    """Primeiras execuções param em aprovação humana de verdade (objeto criado)."""

    original = runtime.agent_engine.run
    calls: list[str] = []

    def run(task):
        calls.append(task.id)
        if len(calls) > times:
            return original(task)
        request, decision = runtime.request_action(
            "process.run", {"command": "echo"}, environment=str(task.environment)
        )
        request.task_id = task.id
        approval = runtime.request_approval(request, decision, task_id=task.id)
        task.status = TaskStatus.REQUIRES_APPROVAL
        task.context["pending_approval"] = approval.id
        task.result = task.result or runtime.new_result()
        runtime.tasks.save(task)
        return task

    monkeypatch.setattr(runtime.agent_engine, "run", run)
    return calls


def test_approval_pauses_the_run_and_resume_finishes_it(runtime, workspace, monkeypatch):
    _write_workflow(workspace, _dag())
    runtime._load_workflows()
    _approval_blocker(runtime, monkeypatch)

    run = runtime.orchestrator.start("dag")

    assert run.status == RunStatus.WAITING
    assert run.step("a").status == StepRunStatus.WAITING

    pending = runtime.approvals.pending_for_task(run.step("a").task_id)
    assert pending
    runtime.approve(pending[0].id, decided_by="vitor", note="ok")

    resumed = runtime.orchestrator.resume(run.id)

    assert resumed.status == RunStatus.COMPLETED
    assert resumed.step("a").status == StepRunStatus.COMPLETED


def test_cancel_marks_run_and_open_tasks(runtime, workspace, monkeypatch):
    _write_workflow(workspace, _dag())
    runtime._load_workflows()
    _approval_blocker(runtime, monkeypatch)

    run = runtime.orchestrator.start("dag")
    cancelled = runtime.orchestrator.cancel(run.id, reason="operador cancelou")

    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.step("b").status == StepRunStatus.CANCELLED
    assert runtime.tasks.get(run.step("a").task_id).status == TaskStatus.CANCELLED


def test_start_rejects_invalid_workflow(runtime, workspace):
    _write_workflow(workspace, {"id": "russo", "steps": [{"id": "a", "depends_on": ["b"]}, {"id": "b"}]})
    runtime._load_workflows()

    with pytest.raises(ConfigError):
        runtime.orchestrator.start("russo")


# ----------------------------------------------------------------------
# agenda (cron)
# ----------------------------------------------------------------------
def test_cron_expression_matching():
    every_minute = CronExpression("* * * * *")
    hourly = CronExpression("0 * * * *")
    weekdays = CronExpression("30 9 * * 1-5")

    assert every_minute.matches(datetime(2026, 9, 16, 10, 47))
    assert hourly.matches(datetime(2026, 9, 16, 10, 0))
    assert not hourly.matches(datetime(2026, 9, 16, 10, 47))
    assert weekdays.matches(datetime(2026, 9, 16, 9, 30))  # 4ª feira
    assert not weekdays.matches(datetime(2026, 9, 19, 9, 30))  # sábado


def test_cron_rejects_bad_expressions():
    with pytest.raises(CronError):
        CronExpression("*/5 * *")
    with pytest.raises(CronError):
        CronExpression("99 * * * *")


def test_scheduler_tick_runs_due_workflows_only_once_per_minute(runtime, workspace):
    payload = _dag()
    payload["trigger"] = {"type": "cron", "cron": "* * * * *"}
    _write_workflow(workspace, payload)
    runtime._load_workflows()
    moment = utcnow().replace(second=0, microsecond=0)

    assert "dag" in [item["workflow"] for item in runtime.scheduler.due(moment)]

    started = [item for item in runtime.scheduler.tick(now=moment) if item.trigger == "cron"]
    assert started and started[0].workflow_id == "dag"

    # mesmo minuto: idempotente, não dispara de novo
    assert runtime.scheduler.tick(now=moment) == []


def test_scheduler_reports_upcoming_runs(runtime, workspace):
    payload = _dag()
    payload["trigger"] = {"type": "cron", "cron": "15 9 * * *"}
    _write_workflow(workspace, payload)
    runtime._load_workflows()

    upcoming = runtime.scheduler.upcoming(now=datetime(2026, 9, 16, 9, 0))

    assert upcoming and upcoming[0]["next_at"] == "2026-09-16T09:15:00"


# ----------------------------------------------------------------------
# gatilhos por evento
# ----------------------------------------------------------------------
def test_event_trigger_starts_the_workflow(runtime, workspace):
    payload = _dag()
    payload["trigger"] = {"type": "event", "event": "invoice.*"}
    _write_workflow(workspace, payload)
    runtime._load_workflows()

    bound = runtime.bind_triggers()
    assert bound >= 1  # o workspace de exemplo também declara gatilhos

    runtime.events.publish("invoice.received", actor="erp", payload={"invoice": "NF-1"})

    runs = runtime.runs.list(workflow_id="dag", limit=5)
    assert runs and runs[0].trigger == "event"
    assert runs[0].inputs["event"] == "invoice.received"


def test_internal_run_events_do_not_feed_back(runtime, workspace):
    payload = _dag()
    payload["trigger"] = {"type": "event", "event": "task.*"}
    _write_workflow(workspace, payload)
    runtime._load_workflows()
    runtime.bind_triggers()

    runtime.events.publish("task.created", actor="runtime", payload={"workflow_run": "run_1"})

    assert runtime.runs.list(workflow_id="dag", limit=5) == []


def test_trigger_pattern_matching():
    assert matches("task.*", "task.created")
    assert matches("task.created", "task.created")
    assert not matches("task.created", "task.completed")
    assert matches("*", "qualquer.coisa")


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def test_api_workflow_endpoints(runtime, workspace):
    from fastapi.testclient import TestClient

    from egr.api.server import create_app

    _write_workflow(workspace, _dag())
    runtime._load_workflows()
    client = TestClient(create_app(runtime))

    assert "dag" in [item["id"] for item in client.get("/v1/workflows").json()]

    started = client.post("/v1/workflows/dag/run", json={"inputs": {"x": 1}})
    assert started.status_code == 200
    assert started.json()["status"] == "completed"

    runs = client.get("/v1/workflow-runs").json()
    assert runs and runs[0]["workflow_id"] == "dag"

    webhook = client.post("/v1/webhooks/dag", json={"inputs": {"origem": "erp"}})
    assert webhook.status_code == 200
    assert webhook.json()["trigger"] == "webhook"

    assert client.post("/v1/workflows/nao-existe/run").status_code == 404
    assert client.post("/v1/webhooks/nao-existe").status_code == 404
