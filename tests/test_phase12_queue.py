"""Fase 12 (2/2) — fila de saída: sistema alheio falha, o Runtime não esquece.

Lacuna 11b: chamada direta não sobrevive a um 503. Agora o pedido entra na fila,
tem tentativa com espera crescente, chave de idempotência e, se esgotar, vira
`failed` com motivo — visível, não silencioso.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from egr.api.server import create_app
from egr.cli.commands.integration import app as integration_app
from egr.core.config import EGRConfig, IntegrationQueueConfig, IntegrationsConfig, Settings
from egr.core.errors import ConfigError
from egr.core.timeutil import utcnow
from egr.domain.enums import EventType, IntegrationKind, JobStatus
from egr.domain.integration import Integration
from egr.integrations.transports import Transport
from egr.runtime.runtime import Runtime
from egr.templates import render_workspace


class FakeResponse:
    def __init__(self, status: int = 200, text: str = '{"ok": true}'):
        self.status_code = status
        self.text = text


class FakeClient:
    def __init__(self, fail_times: int = 0, status: int = 200, text: str = '{"ok": true}'):
        self.fail_times = fail_times
        self.status = status
        self.text = text
        self.requests: list[dict] = []

    def request(self, method, url, headers=None, content=None, json=None, timeout=None):
        self.requests.append({"method": method, "url": url})
        if len(self.requests) <= self.fail_times:
            raise RuntimeError("503 indisponível")
        if self.status >= 400:
            return FakeResponse(self.status, self.text)
        return FakeResponse(self.status, self.text)


class Broken:
    def request(self, *args, **kwargs):
        raise RuntimeError("sem rede")


def conector(rt: Runtime, integration_id: str = "CRM") -> Integration:
    item = Integration(
        id=integration_id,
        name="CRM",
        enabled=True,
        base_url="https://api.exemplo.com/v1",
        allowed_hosts=["api.exemplo.com"],
        allowed_methods=["GET", "POST"],
        read_only=False,
        cost_per_call=0.01,
    )
    rt.connectors.register(item)
    return item


def make_runtime(tmp_path: Path, *, queue: IntegrationQueueConfig | None = None) -> Runtime:
    render_workspace(tmp_path, sample=False)
    config = EGRConfig()
    if queue is not None:
        config.integrations = IntegrationsConfig(queue=queue)
    return Runtime(Settings(workspace=tmp_path, config=config), enable_logging=False)


# --------------------------------------------------------------------------- #
# domínio
# --------------------------------------------------------------------------- #
def test_job_domain_defaults():
    from egr.domain.integration import IntegrationJob

    job = IntegrationJob(id="job_1", integration="CRM")

    assert str(job.status) == "pending"
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.exhausted is False
    assert job.summary()["tentativas"] == "0/3"


def test_backoff_grows_and_has_a_ceiling():
    from egr.domain.integration import IntegrationJob

    job = IntegrationJob(id="job_1", integration="CRM", attempts=1)
    assert job.wait_seconds(base=30) == 30
    job.attempts = 2
    assert job.wait_seconds(base=30) == 60
    job.attempts = 3
    assert job.wait_seconds(base=30) == 120
    job.attempts = 12
    assert job.wait_seconds(base=30, cap=3600) == 3600


# --------------------------------------------------------------------------- #
# enfileirar
# --------------------------------------------------------------------------- #
def test_enqueue_does_not_call_the_system(runtime):
    conector(runtime)
    client = FakeClient()
    runtime.connectors.transport = Transport(client=client)

    job = runtime.connectors.enqueue("CRM", path="/clientes")

    assert str(job.status) == "pending"
    assert job.attempts == 0
    assert client.requests == []
    assert runtime.integration_calls.count() == 0


def test_enqueue_is_idempotent_by_key(runtime):
    conector(runtime)

    first = runtime.connectors.enqueue("CRM", path="/clientes", idempotency="pedido-42")
    second = runtime.connectors.enqueue("CRM", path="/clientes", idempotency="pedido-42")

    assert second.id == first.id
    assert runtime.integration_jobs.count() == 1


def test_enqueue_requires_declared_connector(runtime):
    with pytest.raises(ConfigError):
        runtime.connectors.enqueue("NAO_EXISTE", path="/x")


def test_enqueue_respects_disabled_queue(tmp_path):
    instance = make_runtime(tmp_path, queue=IntegrationQueueConfig(enabled=False))
    conector(instance)

    with pytest.raises(ConfigError) as exc:
        instance.connectors.enqueue("CRM", path="/x")

    assert "fila" in str(exc.value)
    instance.close()


def test_enqueue_is_audited(runtime):
    conector(runtime)
    runtime.connectors.enqueue("CRM", path="/clientes", idempotency="k1")

    tipos = [str(event.type) for event in runtime.audit.list(limit=200)]

    assert EventType.INTEGRATION_JOB_QUEUED in tipos


# --------------------------------------------------------------------------- #
# processar
# --------------------------------------------------------------------------- #
def test_drain_executes_pending_job(runtime):
    conector(runtime)
    client = FakeClient()
    runtime.connectors.transport = Transport(client=client)
    job = runtime.connectors.enqueue("CRM", path="/clientes")

    resultado = runtime.connectors.drain()

    assert resultado[0]["status"] == "done"
    assert client.requests[0]["url"] == "https://api.exemplo.com/v1/clientes"
    salvo = runtime.integration_jobs.get(job.id)
    assert str(salvo.status) == "done"
    assert salvo.call_id
    assert runtime.integration_calls.count() == 1


def test_drain_retries_with_growing_wait(runtime):
    conector(runtime)
    runtime.connectors.transport = Transport(client=Broken())
    job = runtime.connectors.enqueue("CRM", path="/clientes")

    resultado = runtime.connectors.drain()

    assert resultado[0]["status"] == "pending"
    assert resultado[0]["tentativas"] == "1/3"
    salvo = runtime.integration_jobs.get(job.id)
    assert salvo.attempts == 1
    assert salvo.next_attempt is not None
    assert 25 <= (salvo.next_attempt - utcnow()).total_seconds() <= 35


def test_drain_respects_the_wait_window(runtime):
    conector(runtime)
    runtime.connectors.transport = Transport(client=Broken())
    runtime.connectors.enqueue("CRM", path="/clientes")
    runtime.connectors.drain()

    assert runtime.connectors.drain() == []
    assert runtime.integration_jobs.list(limit=1)[0].attempts == 1


def test_drain_gives_up_and_records_the_reason(runtime):
    conector(runtime)
    runtime.connectors.transport = Transport(client=Broken())
    runtime.connectors.enqueue("CRM", path="/clientes", max_attempts=2)

    runtime.connectors.drain()
    job = runtime.integration_jobs.list(limit=1)[0]
    job.next_attempt = utcnow() - timedelta(seconds=1)
    runtime.integration_jobs.update(job)
    resultado = runtime.connectors.drain()

    final = runtime.integration_jobs.get(job.id)
    assert resultado[0]["status"] == "failed"
    assert str(final.status) == "failed"
    assert "sem rede" in final.last_error
    tipos = [str(event.type) for event in runtime.audit.list(limit=200)]
    assert EventType.INTEGRATION_JOB_FAILED in tipos


def test_drain_recovers_after_a_failure(runtime):
    conector(runtime)
    client = FakeClient(fail_times=1)
    runtime.connectors.transport = Transport(client=client)
    runtime.connectors.enqueue("CRM", path="/clientes")

    runtime.connectors.drain()
    job = runtime.integration_jobs.list(limit=1)[0]
    job.next_attempt = utcnow() - timedelta(seconds=1)
    runtime.integration_jobs.update(job)
    resultado = runtime.connectors.drain()

    assert resultado[0]["status"] == "done"
    assert len(client.requests) == 2


def test_drain_of_unknown_connector_is_recorded(runtime):
    conector(runtime)
    client = FakeClient()
    runtime.connectors.transport = Transport(client=client)
    runtime.connectors.enqueue("CRM", path="/clientes")
    runtime.integrations_repository.delete("CRM")
    runtime.connectors.registry.pop("CRM", None)

    resultado = runtime.connectors.drain()

    assert resultado[0]["status"] in ("pending", "failed")
    assert resultado[0]["erro"]


def test_cancel_pending_job(runtime):
    conector(runtime)
    job = runtime.connectors.enqueue("CRM", path="/clientes")

    cancelado = runtime.connectors.cancel(job.id)

    assert str(cancelado.status) == "cancelled"
    assert runtime.connectors.drain() == []


def test_cancel_unknown_or_done_job(runtime):
    conector(runtime)
    runtime.connectors.transport = Transport(client=FakeClient())
    job = runtime.connectors.enqueue("CRM", path="/clientes")
    runtime.connectors.drain()

    with pytest.raises(ConfigError):
        runtime.connectors.cancel("job_inexistente")
    with pytest.raises(ConfigError) as exc:
        runtime.connectors.cancel(job.id)
    assert "já foi executado" in str(exc.value)


def test_status_reports_the_queue(runtime):
    conector(runtime)
    runtime.connectors.enqueue("CRM", path="/clientes")

    fila = runtime.integrations_status()["fila"]

    assert fila["habilitada"] is True
    assert fila["tentativas"] == 3
    assert fila["por_status"].get("pending") == 1


def test_health_flags_exhausted_jobs(runtime):
    conector(runtime)
    runtime.connectors.transport = Transport(client=Broken())
    runtime.connectors.enqueue("CRM", path="/clientes", max_attempts=1)
    runtime.connectors.drain()

    checks = {item["check"]: item for item in runtime.health()["checks"]}

    assert checks["integracoes:fila"]["ok"] is False


def test_job_repository_stats_and_filters(runtime):
    conector(runtime)
    runtime.connectors.transport = Transport(client=FakeClient())
    runtime.connectors.enqueue("CRM", path="/a")
    runtime.connectors.enqueue("CRM", path="/b")
    runtime.connectors.drain()

    assert runtime.integration_jobs.count() == 2
    assert runtime.integration_jobs.stats()["done"] == 2
    assert len(runtime.integration_jobs.list(status="done")) == 2
    assert runtime.integration_jobs.list(status="failed") == []
    assert runtime.integration_jobs.due(utcnow()) == []


def test_job_exhausted_property():
    from egr.domain.integration import IntegrationJob

    job = IntegrationJob(id="j", integration="CRM", attempts=3, max_attempts=3)

    assert job.exhausted is True
    assert job.status == JobStatus.PENDING


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def sql_connector(rt: Runtime) -> Integration:
    """Conector de verdade e sem rede: o banco do próprio workspace."""

    item = Integration(
        id="WAREHOUSE",
        name="Banco do workspace",
        type=IntegrationKind.SQL,
        enabled=True,
        dsn=f"sqlite:///{rt.settings.workspace / '.egr' / 'egr.db'}",
        read_only=True,
    )
    rt.connectors.register(item)
    return item


def test_cli_enqueue_jobs_and_drain(tmp_path):
    instance = make_runtime(tmp_path)
    sql_connector(instance)
    runner = CliRunner()

    enqueue = runner.invoke(
        integration_app, ["enqueue", "WAREHOUSE", "", "--query", "select 1 as ok", "-k", "k1", "-w", str(tmp_path)]
    )
    assert enqueue.exit_code == 0, enqueue.output
    assert "na fila" in enqueue.output

    jobs = runner.invoke(integration_app, ["jobs", "-w", str(tmp_path)])
    assert jobs.exit_code == 0
    assert "pending" in jobs.output

    drain = runner.invoke(integration_app, ["drain", "-w", str(tmp_path)])
    assert drain.exit_code == 0
    assert "done" in drain.output
    instance.close()


def test_cli_jobs_empty_queue(tmp_path):
    instance = make_runtime(tmp_path)
    runner = CliRunner()

    result = runner.invoke(integration_app, ["jobs", "-w", str(tmp_path)])

    assert result.exit_code == 0
    assert "fila vazia" in result.output
    instance.close()


def test_cli_cancel_job(tmp_path):
    instance = make_runtime(tmp_path)
    sql_connector(instance)
    job = instance.connectors.enqueue("WAREHOUSE", query="select 1 as ok")
    runner = CliRunner()

    result = runner.invoke(integration_app, ["cancel", job.id, "-w", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "cancelado" in result.output
    instance.close()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def test_api_enqueue_list_drain_and_cancel(runtime):
    conector(runtime)
    runtime.connectors.transport = Transport(client=FakeClient())
    client = TestClient(create_app(runtime))

    criado = client.post("/v1/integrations/CRM/jobs", json={"path": "/clientes", "idempotency": "api-1"})
    assert criado.status_code == 200
    assert criado.json()["status"] == "pending"

    repetido = client.post("/v1/integrations/CRM/jobs", json={"path": "/clientes", "idempotency": "api-1"})
    assert repetido.json()["id"] == criado.json()["id"]

    assert len(client.get("/v1/integrations/jobs").json()) == 1

    processados = client.post("/v1/integrations/jobs/drain").json()
    assert processados[0]["status"] == "done"

    outro = client.post("/v1/integrations/CRM/jobs", json={"path": "/outro"}).json()
    cancelado = client.delete(f"/v1/integrations/jobs/{outro['id']}")
    assert cancelado.status_code == 200
    assert cancelado.json()["status"] == "cancelled"


def test_api_enqueue_unknown_connector_is_400(runtime):
    client = TestClient(create_app(runtime))

    response = client.post("/v1/integrations/NAO_EXISTE/jobs", json={"path": "/x"})

    assert response.status_code == 400
