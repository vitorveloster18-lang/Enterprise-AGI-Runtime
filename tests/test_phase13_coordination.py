"""Fase 13 — lacuna 6b: coordenação negociada e gatilhos de banco.

Duas ausências da Fase 6:

- **quem executa** era escolha do humano ou do primeiro da lista. Agora os
  agentes elegíveis dão um lance e uma estratégia declarada escolhe — com
  motivo registrado e recusa visível para quem não pode;
- **o que dispara um workflow** era evento do Runtime, cron ou webhook. Faltava
  o caso mais comum no mundo real: alguém mexeu no banco.

Nenhum dos dois é automático escondido: negociação e handoff passam por política
e permissão, e o gatilho de banco só aceita expressão sobre colunas liberadas.
"""

from __future__ import annotations

import pytest

from egr.core.config import EGRConfig, Settings
from egr.core.errors import ConfigError
from egr.core.ids import new_id
from egr.domain.coordination import DatabaseTrigger
from egr.domain.enums import EventType, TaskStatus
from egr.domain.task import Task
from egr.runtime.runtime import Runtime
from egr.storage.triggers import validate_expression


def _locked(runtime: Runtime, **overrides) -> Runtime:
    config = EGRConfig()
    for key, value in overrides.items():
        setattr(config.coordination, key, value)
    return Runtime(Settings(workspace=runtime.settings.workspace, config=config), enable_logging=False)


def _task(runtime: Runtime, agent: str = "finance-agent", status: TaskStatus = TaskStatus.PENDING) -> Task:
    task = Task(id=new_id("task"), objective="conciliar lançamentos", agent_id=agent)
    task.status = status
    return runtime.tasks.save(task)


# ----------------------------------------------------------------------
# negociação
# ----------------------------------------------------------------------
def test_negotiation_collects_a_bid_from_every_eligible_agent(runtime):
    negotiation = runtime.coordinator.negotiate("conciliar lançamentos do dia")

    names = {bid.agent for bid in negotiation.bids}

    assert names == set(runtime.agents)
    assert all(bid.allowed for bid in negotiation.bids)
    assert negotiation.chosen in names


def test_the_choice_is_registered_with_its_reason(runtime):
    negotiation = runtime.coordinator.negotiate("conciliar lançamentos do dia", actor="human:vitor")

    stored = runtime.negotiations.get(negotiation.id)

    assert stored is not None
    assert stored.chosen == negotiation.chosen
    assert "equilibrado" in stored.reason
    kinds = {event.type for event in runtime.audit.list(limit=200)}
    assert EventType.TASK_NEGOTIATED in kinds


def test_negotiation_is_deterministic(runtime):
    first = runtime.coordinator.negotiate("mesmo objetivo")
    second = runtime.coordinator.negotiate("mesmo objetivo")

    assert first.chosen == second.chosen
    assert [bid.score for bid in first.bids] == [bid.score for bid in second.bids]


def test_who_cannot_run_is_vetoed_with_a_reason(runtime):
    runtime.agents["finance-agent"].metadata["disabled"] = True

    negotiation = runtime.coordinator.negotiate("conciliar")

    bid = next(item for item in negotiation.bids if item.agent == "finance-agent")
    assert bid.allowed is False
    assert bid.veto == "agente desabilitado"
    assert bid.agent not in (negotiation.chosen or "")


def test_capability_filters_the_dispute(runtime):
    negotiation = runtime.coordinator.negotiate("resumir contrato", capability="reasoning")

    assert negotiation.capability == "reasoning"
    assert negotiation.chosen

    impossible = runtime.coordinator.negotiate("resumir contrato", capability="capacidade-que-nao-existe")
    assert impossible.chosen is None
    assert "nenhum agente elegível" in impossible.reason


def test_strategy_looks_at_the_queue(runtime):
    busy = _task(runtime, agent="document-agent", status=TaskStatus.RUNNING)
    assert busy.agent_id == "document-agent"

    negotiation = runtime.coordinator.negotiate("conciliar", strategy="menor_fila")

    assert negotiation.chosen == "finance-agent"
    assert "menor_fila" in negotiation.reason


def test_declared_strategy_uses_the_fixed_agent(runtime):
    instance = _locked(runtime, strategy="declarado", default_agent="runtime-agent")
    try:
        negotiation = instance.coordinator.negotiate("conciliar")

        assert negotiation.chosen == "runtime-agent"
        assert "declarada" in negotiation.reason
    finally:
        instance.close()


def test_declared_strategy_without_an_agent_is_refused(runtime):
    instance = _locked(runtime, strategy="declarado", default_agent="nao-existe")
    try:
        with pytest.raises(ConfigError, match="não está carregado"):
            instance.coordinator.negotiate("conciliar")
    finally:
        instance.close()


def test_unknown_strategy_is_refused(runtime):
    with pytest.raises(ConfigError, match="estratégia inválida"):
        runtime.coordinator.negotiate("conciliar", strategy="menor_nome")


def test_disabled_coordination_says_so(runtime):
    instance = _locked(runtime, enabled=False)
    try:
        with pytest.raises(ConfigError, match="desligada"):
            instance.coordinator.negotiate("conciliar")
    finally:
        instance.close()


# ----------------------------------------------------------------------
# handoff
# ----------------------------------------------------------------------
def test_handoff_passes_the_task_along_with_a_reason(runtime):
    task = _task(runtime)

    moved = runtime.coordinator.handoff(task.id, "document-agent", reason="precisa de visão de documento")

    assert moved.agent_id == "document-agent"
    assert moved.context["handoffs"][0]["de"] == "finance-agent"
    assert moved.context["handoffs"][0]["motivo"] == "precisa de visão de documento"
    kinds = {event.type for event in runtime.audit.list(limit=200)}
    assert EventType.TASK_HANDOFF in kinds


def test_handoff_unblocks_a_waiting_task(runtime):
    task = _task(runtime, status=TaskStatus.WAITING)

    moved = runtime.coordinator.handoff(task.id, "document-agent")

    assert moved.status == TaskStatus.PENDING


def test_handoff_refuses_what_it_cannot_do(runtime):
    task = _task(runtime)

    with pytest.raises(ConfigError, match="não está carregado"):
        runtime.coordinator.handoff(task.id, "nao-existe")

    with pytest.raises(ConfigError, match="já está com"):
        runtime.coordinator.handoff(task.id, "finance-agent")

    runtime.agents["document-agent"].metadata["disabled"] = True
    with pytest.raises(ConfigError, match="desabilitado"):
        runtime.coordinator.handoff(task.id, "document-agent")

    done = _task(runtime, status=TaskStatus.COMPLETED)
    with pytest.raises(ConfigError, match="não dá mais para repassar"):
        runtime.coordinator.handoff(done.id, "document-agent")


def test_handoff_has_a_limit(runtime):
    instance = _locked(runtime, max_handoffs=1)
    try:
        task = _task(instance)
        instance.coordinator.handoff(task.id, "document-agent")

        with pytest.raises(ConfigError, match="limite 1"):
            instance.coordinator.handoff(task.id, "runtime-agent")
    finally:
        instance.close()


def test_handoff_needs_permission_when_identity_is_required(runtime):
    from egr.security.identity import PrincipalKind

    config = EGRConfig()
    config.security.identity_required = True
    instance = Runtime(Settings(workspace=runtime.settings.workspace, config=config), enable_logging=False)
    try:
        instance.identity.create_principal("leitor", roles=["viewer"], kind=PrincipalKind.HUMAN)
        instance.identity.create_principal("operador", roles=["operator"], kind=PrincipalKind.HUMAN)
        task = _task(instance)

        from egr.core.errors import AuthorizationError

        with pytest.raises(AuthorizationError, match=r"task\.handoff"):
            instance.coordinator.handoff(task.id, "document-agent", actor="human:leitor")

        moved = instance.coordinator.handoff(task.id, "document-agent", actor="human:operador")
        assert moved.agent_id == "document-agent"
    finally:
        instance.close()


# ----------------------------------------------------------------------
# gatilhos de banco
# ----------------------------------------------------------------------
def _trigger(**overrides) -> DatabaseTrigger:
    payload = {
        "id": new_id("trg"),
        "name": "task falhou",
        "table": "tasks",
        "event": "update",
        "when": "NEW.status = 'failed'",
        "emit": "db.task_failed",
    }
    payload.update(overrides)
    return DatabaseTrigger(**payload)


def test_install_creates_a_real_trigger_in_the_database(runtime):
    trigger = runtime.db_trigger_manager.install(_trigger())

    names = {
        row["name"]
        for row in runtime.db.query("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }

    assert trigger.trigger_name in names
    assert runtime.db_triggers.get(trigger.id) is not None
    kinds = {event.type for event in runtime.audit.list(limit=200)}
    assert EventType.DB_TRIGGER_INSTALLED in kinds


def test_the_expression_is_not_free_sql(runtime):
    with pytest.raises(ConfigError, match="não reconhecido"):
        runtime.db_trigger_manager.install(_trigger(when="NEW.status = 'x'; DROP TABLE tasks"))
    with pytest.raises(ConfigError, match="fora da lista branca"):
        runtime.db_trigger_manager.install(_trigger(when="NEW.segredo = 'x'"))
    with pytest.raises(ConfigError, match="fora da lista branca"):
        runtime.db_trigger_manager.install(_trigger(table="events"))
    with pytest.raises(ConfigError, match="evento emitido inválido"):
        runtime.db_trigger_manager.install(_trigger(emit="Péssimo Nome"))
    with pytest.raises(ConfigError, match="evento 'upsert' inválido"):
        runtime.db_trigger_manager.install(_trigger(event="upsert"))


def test_old_is_not_allowed_on_insert(runtime):
    with pytest.raises(ConfigError, match="não existe em gatilho de insert"):
        validate_expression("OLD.status = 'failed'", "tasks", "insert")


def test_a_changed_row_becomes_a_runtime_event(runtime):
    runtime.db_trigger_manager.install(_trigger())
    task = _task(runtime)

    runtime.db.execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (task.id,))
    runtime.db.commit()

    assert runtime.db_events.count(pending_only=True) == 1
    pending = runtime.db_events.pending()
    assert pending[0].event == "db.task_failed"
    assert pending[0].payload["status"] == "failed"
    assert pending[0].row_id == task.id


def test_drain_emits_once_and_marks_it(runtime):
    runtime.db_trigger_manager.install(_trigger())
    task = _task(runtime)
    runtime.db.execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (task.id,))
    runtime.db.commit()

    processed = runtime.db_trigger_manager.drain()
    types = {str(event.type) for event in runtime.audit.list(limit=200)}

    assert len(processed) == 1
    assert "db.task_failed" in types
    assert EventType.DB_TRIGGER_FIRED in types
    assert runtime.db_events.count(pending_only=True) == 0

    # segunda passada: a fila já foi drenada (evento de banco não se repete)
    assert runtime.db_trigger_manager.drain() == []


def test_drain_starts_a_workflow_listening_to_the_event(runtime, workspace):
    import yaml

    # o gatilho por evento lê os workflows do disco: declarar é escrever o YAML
    directory = workspace / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reagir-falha.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "reagir-falha",
                "name": "Reagir a falha",
                "steps": [{"id": "passo", "agent": "finance-agent", "objective": "investigar a falha"}],
                "trigger": {"type": "event", "event": "db.task_failed"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    runtime._load_workflows()
    runtime.bind_triggers()
    workflow_id = "reagir-falha"
    runtime.db_trigger_manager.install(_trigger())
    task = _task(runtime)
    runtime.db.execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (task.id,))
    runtime.db.commit()

    runtime.db_trigger_manager.drain()

    runs = [run for run in runtime.runs.list(limit=20) if run.workflow_id == workflow_id]
    assert runs, "o evento de banco devia ter disparado o workflow"


def test_uninstall_removes_the_trigger(runtime):
    trigger = runtime.db_trigger_manager.install(_trigger())

    runtime.db_trigger_manager.uninstall(trigger.id)

    names = {
        row["name"]
        for row in runtime.db.query("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    assert trigger.trigger_name not in names
    assert runtime.db_triggers.get(trigger.id) is None


def test_triggers_survive_a_restart(runtime):
    runtime.db_trigger_manager.install(_trigger(emit="db.task_failed"))

    restarted = Runtime(Settings(workspace=runtime.settings.workspace, config=EGRConfig()), enable_logging=False)
    try:
        assert restarted.installed_db_triggers == 1
    finally:
        restarted.close()


def test_status_reports_coordination_and_triggers(runtime):
    runtime.db_trigger_manager.install(_trigger())
    runtime.coordinator.negotiate("conciliar")

    state = runtime.coordination_status()

    assert state["estratégia"] == "equilibrado"
    assert state["total"] == 1
    assert state["gatilhos_de_banco"]["fila"] == 0
    assert "tasks" in state["gatilhos_de_banco"]["tabelas_permitidas"]
    assert "coordenação" in runtime.orchestration_status()


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _cli(workspace, module):
    from typer.testing import CliRunner

    return CliRunner(), module


def test_cli_negotiate_and_handoff(workspace):
    from egr.cli.commands.tasks import app as task_app

    instance = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    task = _task(instance)
    instance.close()

    runner, app = _cli(workspace, task_app)

    negotiated = runner.invoke(
        app, ["negotiate", "conciliar lançamentos", "-w", str(workspace)]
    )
    assert negotiated.exit_code == 0, negotiated.output
    assert "escolhido" in negotiated.output

    moved = runner.invoke(
        app,
        ["handoff", task.id, "--to", "document-agent", "--reason", "visão de documento", "-w", str(workspace)],
    )
    assert moved.exit_code == 0, moved.output
    assert "repassada" in moved.output


def test_cli_db_triggers_refuse_bad_expressions(workspace):
    from egr.cli.commands.database import app as db_app

    runner, app = _cli(workspace, db_app)

    bad = runner.invoke(
        app,
        [
            "trigger-add",
            "invasor",
            "--on",
            "tasks",
            "--event",
            "update",
            "--when",
            "NEW.status = 'x'; DROP TABLE tasks",
            "--emit",
            "db.nope",
            "-w",
            str(workspace),
        ],
    )
    assert bad.exit_code == 1
    assert "recusada" in bad.output

    good = runner.invoke(
        app,
        [
            "trigger-add",
            "falha",
            "--on",
            "tasks",
            "--event",
            "update",
            "--when",
            "NEW.status = 'failed'",
            "--emit",
            "db.task_failed",
            "-w",
            str(workspace),
        ],
    )
    assert good.exit_code == 0, good.output
    assert "instalado" in good.output

    listed = runner.invoke(app, ["triggers", "-w", str(workspace)])
    assert listed.exit_code == 0
    assert "falha" in listed.output
    assert "tasks" in listed.output

    drained = runner.invoke(app, ["drain", "-w", str(workspace)])
    assert drained.exit_code == 0, drained.output


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def test_api_coordination_and_database_routes(runtime):
    from fastapi.testclient import TestClient

    from egr.api.server import create_app

    client = TestClient(create_app(runtime))

    negotiated = client.post("/v1/tasks/negotiate", json={"objective": "conciliar lançamentos"})
    assert negotiated.status_code == 200
    assert negotiated.json()["escolhido"] in runtime.agents

    task = _task(runtime)
    handed = client.post(f"/v1/tasks/{task.id}/handoff", json={"to": "document-agent", "reason": "teste"})
    assert handed.status_code == 200, handed.text
    assert handed.json()["agente"] == "document-agent"

    missing = client.post("/v1/tasks/nao-existe/handoff", json={"to": "document-agent"})
    assert missing.status_code == 409

    installed = client.post(
        "/v1/db/triggers",
        json={
            "name": "task falhou",
            "table": "tasks",
            "event": "update",
            "when": "NEW.status = 'failed'",
            "emit": "db.task_failed",
        },
    )
    assert installed.status_code == 200, installed.text
    trigger_id = installed.json()["id"]

    rejected = client.post(
        "/v1/db/triggers",
        json={"name": "invasor", "table": "tasks", "event": "update", "when": "1=1; DROP TABLE tasks", "emit": "db.no"},
    )
    assert rejected.status_code == 422

    runtime.db.execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (task.id,))
    runtime.db.commit()

    drained = client.post("/v1/db/drain")
    assert drained.status_code == 200
    assert drained.json()["processados"] == 1

    events = client.get("/v1/db/events")
    assert events.status_code == 200
    assert events.json()[0]["processado"] is True

    removed = client.delete(f"/v1/db/triggers/{trigger_id}")
    assert removed.status_code == 200
    assert client.delete("/v1/db/triggers/nao-existe").status_code == 404


# ----------------------------------------------------------------------
# configuração
# ----------------------------------------------------------------------
def test_coordination_defaults():
    config = EGRConfig()

    assert config.coordination.enabled is True
    assert config.coordination.strategy == "equilibrado"
    assert config.coordination.max_handoffs == 3
    assert config.coordination.default_agent == ""
