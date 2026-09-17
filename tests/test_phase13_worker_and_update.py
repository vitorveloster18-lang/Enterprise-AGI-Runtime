"""Lacuna 12b — fila com alguém olhando, e pack que evolui sem atropelar.

Duas promessas estavam abertas:

1. a fila de saída tinha `drain` (uma rodada), mas ninguém para ficar chamando
   — o `QueueWorker` é esse alguém, explícito e com parada limpa;
2. atualizar um pack sobrescrevia (ou evitava) no escuro — agora o plano diz
   o que é novo, o que pode mudar e o que foi editado aqui.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from egr.core.config import EGRConfig, IntegrationQueueConfig, IntegrationsConfig, Settings, dump_config
from egr.core.timeutil import utcnow
from egr.domain.enums import EventType, ProposalStatus
from egr.domain.integration import Integration, IntegrationKind
from egr.integrations.worker import QueueWorker
from egr.runtime.runtime import Runtime
from egr.templates import render_workspace


# ----------------------------------------------------------------------
# apoio
# ----------------------------------------------------------------------
def make_runtime(tmp_path: Path, *, queue: IntegrationQueueConfig | None = None) -> Runtime:
    render_workspace(tmp_path, sample=False)
    config = EGRConfig()
    if queue is not None:
        config.integrations = IntegrationsConfig(queue=queue)
    return Runtime(Settings(workspace=tmp_path, config=config), enable_logging=False)


def sql_connector(rt: Runtime, integration_id: str = "WAREHOUSE") -> Integration:
    """Conector de verdade e sem rede: o banco do próprio workspace."""

    item = Integration(
        id=integration_id,
        name="Banco do workspace",
        type=IntegrationKind.SQL,
        enabled=True,
        dsn=f"sqlite:///{rt.settings.workspace / '.egr' / 'egr.db'}",
        read_only=True,
    )
    rt.connectors.register(item)
    return item


class Recorder:
    """sleep de mentira: o laço não espera, mas o que ele esperou fica dito."""

    def __init__(self):
        self.waits: list[float] = []
        self.messages: list[str] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)

    def log(self, message: str) -> None:
        self.messages.append(message)


# ----------------------------------------------------------------------
# 1. dreno em background
# ----------------------------------------------------------------------
def test_round_processes_what_is_due(tmp_path):
    runtime = make_runtime(tmp_path)
    sql_connector(runtime)
    runtime.connectors.enqueue("WAREHOUSE", query="select 1 as ok")
    worker = QueueWorker(runtime, logger=lambda message: None)

    report = worker.round()

    assert report["processados"] == 1
    assert report["por_status"] == {"done": 1}
    assert not report["erro"]
    assert worker.processed == 1
    runtime.close()


def test_round_reports_nothing_to_do(tmp_path):
    runtime = make_runtime(tmp_path)
    sql_connector(runtime)
    worker = QueueWorker(runtime)

    report = worker.round()

    assert report["processados"] == 0 and report["por_status"] == {}
    runtime.close()


def test_round_survives_a_broken_provider(tmp_path):
    """Provedor fora do ar derruba o job, não o processo."""

    runtime = make_runtime(tmp_path)
    sql_connector(runtime)
    runtime.connectors.enqueue("WAREHOUSE", query="select 1 as ok")

    def broken(*args, **kwargs):
        raise RuntimeError("banco alheio caiu")

    runtime.connectors.drain = broken  # type: ignore[method-assign]
    worker = QueueWorker(runtime)

    report = worker.round()

    assert report["processados"] == 0
    assert "banco alheio caiu" in report["erro"]
    runtime.close()


def test_worker_stops_when_queue_is_disabled(tmp_path):
    runtime = make_runtime(tmp_path, queue=IntegrationQueueConfig(enabled=False))
    recorder = Recorder()
    worker = QueueWorker(runtime, sleep=recorder, logger=recorder.log)

    summary = worker.run(max_rounds=3)

    assert summary["rodadas"] == 0
    assert worker.stopped
    assert recorder.waits == []  # nem dorme: não há o que fazer
    assert any("desabilitada" in message for message in recorder.messages)
    runtime.close()


def test_idle_grows_the_wait_with_a_ceiling(tmp_path):
    """Fila parada não martela o banco: a espera dobra até o teto."""

    runtime = make_runtime(tmp_path)
    recorder = Recorder()
    worker = QueueWorker(runtime, interval=10.0, max_interval=25.0, sleep=recorder)

    worker.run(max_rounds=4, stop=lambda: False)

    assert recorder.waits == [10.0, 20.0, 25.0]  # base, dobro, teto
    assert len(worker.history) == 4
    runtime.close()


def test_busy_rounds_keep_the_base_interval(tmp_path):
    runtime = make_runtime(tmp_path)
    sql_connector(runtime)
    runtime.connectors.enqueue("WAREHOUSE", query="select 1 as ok")
    recorder = Recorder()
    worker = QueueWorker(runtime, interval=5.0, sleep=recorder)

    worker.run(max_rounds=3, stop=lambda: False)

    assert recorder.waits == [5.0, 5.0]  # rodada com trabalho mantém o intervalo
    runtime.close()


def test_stop_ends_the_loop(tmp_path):
    runtime = make_runtime(tmp_path)
    recorder = Recorder()
    worker = QueueWorker(runtime, interval=1.0, sleep=recorder)

    worker.run(max_rounds=0, stop=lambda: worker.rounds >= 2)

    assert worker.rounds == 2
    assert worker.summary()["parado"] is False
    runtime.close()


def test_worker_handles_batch_limit(tmp_path):
    runtime = make_runtime(tmp_path)
    sql_connector(runtime)
    for index in range(3):
        runtime.connectors.enqueue("WAREHOUSE", query=f"select {index} as ok")

    worker = QueueWorker(runtime, batch=2)
    report = worker.round()

    assert report["processados"] == 2
    runtime.close()


def test_worker_history_keeps_the_last_reports(tmp_path):
    runtime = make_runtime(tmp_path)
    worker = QueueWorker(runtime, interval=1.0, sleep=Recorder())

    worker.run(max_rounds=5, stop=lambda: False)

    assert len(worker.history) == 5
    assert len(worker.summary()["últimas"]) == 5
    runtime.close()


def test_cli_worker_runs_a_round_and_reports(tmp_path):
    from egr.cli.commands.integration import app as integration_app

    runtime = make_runtime(tmp_path)
    sql_connector(runtime)
    runtime.connectors.enqueue("WAREHOUSE", query="select 1 as ok")
    runtime.close()

    result = CliRunner().invoke(
        integration_app, ["worker", "--interval", "0.5", "--rounds", "1", "-w", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert "processados" in result.output


def test_cli_worker_refuses_disabled_queue(tmp_path):
    from egr.cli.commands.integration import app as integration_app

    runtime = make_runtime(tmp_path, queue=IntegrationQueueConfig(enabled=False))
    (tmp_path / "egr.yaml").write_text(dump_config(runtime.settings.config))
    runtime.close()

    result = CliRunner().invoke(integration_app, ["worker", "--rounds", "1", "-w", str(tmp_path)])

    assert result.exit_code == 1
    assert "desabilitada" in result.output


# ----------------------------------------------------------------------
# 2. atualização de pack com conteúdo editado
# ----------------------------------------------------------------------
def install(runtime: Runtime, pack_id: str = "finance", *, actor: str = "human:vitor") -> str:
    proposal = runtime.packs.propose(pack_id, actor=actor)
    runtime.workbench.approve(proposal.id, actor=actor)
    runtime.workbench.apply(proposal.id, actor=actor)
    return proposal.id


def bump(runtime: Runtime, version: str, pack_id: str = "finance") -> None:
    """O workspace sobrepõe o catálogo: é assim que uma versão nova chega."""

    path = runtime.settings.workspace / "packs" / f"{pack_id}.yaml"
    content = path.read_text(encoding="utf-8")
    path.write_text(content.replace("version: 1.0.0", f"version: {version}", 1), encoding="utf-8")


def test_update_plan_classifies_files(tmp_path):
    runtime = make_runtime(tmp_path)
    install(runtime)
    edited = runtime.settings.workspace / "agents" / "cashflow-agent.yaml"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n# ajuste local\n", encoding="utf-8")

    plan = runtime.packs.update_plan("finance")

    assert plan["de"] == "1.0.0"
    assert "agents/cashflow-agent.yaml" in plan["conflito"]
    assert "workflows/fechamento-caixa.yaml" in plan["atualizável"]
    assert plan["idêntico"] is False
    runtime.close()


def test_manifest_is_always_part_of_the_update(tmp_path):
    """O manifesto é o alvo da proposta: ele não pode ficar para trás."""

    runtime = make_runtime(tmp_path)
    install(runtime)
    bump(runtime, "1.1.0")

    plan = runtime.packs.update_plan("finance")

    assert "packs/finance.yaml" in plan["atualizável"]
    runtime.close()


def test_update_plan_needs_the_pack_installed(tmp_path):
    runtime = make_runtime(tmp_path)

    with pytest.raises(Exception, match="não instalado"):
        runtime.packs.update_plan("finance")
    runtime.close()


def test_update_refuses_when_nothing_changed(tmp_path):
    runtime = make_runtime(tmp_path)
    install(runtime)

    with pytest.raises(Exception, match="nada a atualizar"):
        runtime.packs.update("finance", actor="human:vitor")
    runtime.close()


def test_update_proposal_carries_the_plan(tmp_path):
    runtime = make_runtime(tmp_path)
    install(runtime)
    bump(runtime, "1.1.0")

    proposal = runtime.packs.update("finance", actor="human:vitor")

    metadata = proposal.metadata["pack_update"]
    assert metadata["de"] == "1.0.0" and metadata["para"] == "1.1.0"
    assert metadata["sobrescrever"] is False
    assert metadata["overwrite"] == []
    assert str(proposal.status) in (str(ProposalStatus.VALIDATED), str(ProposalStatus.DRAFT))
    runtime.close()


def test_update_preserves_what_was_edited_here(tmp_path):
    runtime = make_runtime(tmp_path)
    install(runtime)
    edited = runtime.settings.workspace / "agents" / "cashflow-agent.yaml"
    original = edited.read_text(encoding="utf-8")
    edited.write_text(original + "\n# ajuste local\n", encoding="utf-8")
    bump(runtime, "1.1.0")

    proposal = runtime.packs.update("finance", actor="human:vitor")
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id, actor="human:vitor")

    assert "# ajuste local" in edited.read_text(encoding="utf-8")  # ninguém perdeu trabalho
    assert runtime.packs_repository.get("finance").version == "1.1.0"
    assert runtime.packs_repository.get("finance").files  # registro íntegro
    runtime.close()


def test_update_with_overwrite_replaces_edited_files(tmp_path):
    runtime = make_runtime(tmp_path)
    install(runtime)
    edited = runtime.settings.workspace / "agents" / "cashflow-agent.yaml"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n# ajuste local\n", encoding="utf-8")
    bump(runtime, "1.1.0")

    proposal = runtime.packs.update("finance", actor="human:vitor", overwrite=True)
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id, actor="human:vitor")

    assert "# ajuste local" not in edited.read_text(encoding="utf-8")
    assert runtime.packs.update_plan("finance")["idêntico"] is True
    runtime.close()


def test_updating_records_an_audit_event(tmp_path):
    runtime = make_runtime(tmp_path)
    install(runtime)
    bump(runtime, "1.1.0")

    proposal = runtime.packs.update("finance", actor="human:vitor")
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id, actor="human:vitor")

    kinds = [event.type for event in runtime.audit.list(limit=100)]
    assert EventType.PACK_UPDATED in kinds
    runtime.close()


def test_preserved_file_keeps_its_recorded_checksum(tmp_path):
    """Preservar não é esquecer: o arquivo continua registrado com a impressão atual."""

    runtime = make_runtime(tmp_path)
    install(runtime)
    edited = runtime.settings.workspace / "agents" / "cashflow-agent.yaml"
    edited.write_text(edited.read_text(encoding="utf-8") + "\n# ajuste local\n", encoding="utf-8")
    bump(runtime, "1.1.0")
    proposal = runtime.packs.update("finance", actor="human:vitor")
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id, actor="human:vitor")

    record = runtime.packs_repository.get("finance")

    assert record.checksums.get("agents/cashflow-agent.yaml")
    assert len(record.files) == 7
    runtime.close()


def test_cli_update_shows_the_plan_and_proposes(tmp_path):
    from egr.cli.commands.pack import app as pack_app

    runtime = make_runtime(tmp_path)
    install(runtime)
    bump(runtime, "1.1.0")
    (runtime.settings.workspace / "agents" / "cashflow-agent.yaml").write_text(
        (runtime.settings.workspace / "agents" / "cashflow-agent.yaml").read_text(encoding="utf-8")
        + "\n# ajuste local\n",
        encoding="utf-8",
    )
    runtime.close()

    runner = CliRunner()
    plain = runner.invoke(pack_app, ["update", "finance", "-w", str(tmp_path)])
    as_json = runner.invoke(pack_app, ["update", "finance", "--json", "-w", str(tmp_path)])

    assert plain.exit_code == 0, plain.output
    assert "conflito" in plain.output and "preservado" in plain.output
    assert as_json.exit_code == 0
    assert json.loads(as_json.output)["plano"]["para"] == "1.1.0"


def test_cli_update_says_when_there_is_nothing_to_do(tmp_path):
    from egr.cli.commands.pack import app as pack_app

    runtime = make_runtime(tmp_path)
    install(runtime)
    runtime.close()

    result = CliRunner().invoke(pack_app, ["update", "finance", "-w", str(tmp_path)])

    assert result.exit_code == 0
    assert "nada a atualizar" in result.output


# ----------------------------------------------------------------------
# 3. coerência
# ----------------------------------------------------------------------
def test_queue_due_window_is_respected_by_the_worker(tmp_path):
    """Job recém-falhado volta a dormir: o worker não insiste fora de hora."""

    runtime = make_runtime(tmp_path, queue=IntegrationQueueConfig(backoff_seconds=30))
    broken_connector(runtime)
    runtime.connectors.enqueue("WAREHOUSE", query="select 1 as ok")
    worker = QueueWorker(runtime)

    first = worker.round()
    second = worker.round()

    assert first["por_status"] == {"pending": 1}
    assert second["processados"] == 0  # espera não venceu: não insiste
    due = runtime.integration_jobs.due(utcnow())
    assert due == []
    runtime.close()


class Broken:
    def request(self, *args, **kwargs):
        raise RuntimeError("sem rede")


def broken_connector(rt: Runtime) -> Integration:
    item = Integration(
        id="WAREHOUSE",
        name="Banco do workspace",
        type=IntegrationKind.REST,
        enabled=True,
        base_url="https://indisponivel.exemplo.com",
        allowed_hosts=["indisponivel.exemplo.com"],
        allowed_methods=["GET"],
        read_only=True,
    )
    rt.connectors.register(item)
    rt.connectors.transport = Broken()  # type: ignore[assignment]
    return item


def test_pack_yaml_stays_valid_after_update(tmp_path):
    runtime = make_runtime(tmp_path)
    install(runtime)
    bump(runtime, "1.1.0")
    proposal = runtime.packs.update("finance", actor="human:vitor")
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id, actor="human:vitor")

    manifest = yaml.safe_load((runtime.settings.workspace / "packs" / "finance.yaml").read_text(encoding="utf-8"))

    assert manifest["version"] == "1.1.0"
    runtime.close()
