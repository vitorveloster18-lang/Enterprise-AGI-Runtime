"""Fase 11 — Enterprise Integrations.

Sistema externo é fronteira: o conector é declarado, a credencial vive no cofre,
a política decide e cada chamada fica registrada com latência, custo e ator.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from egr.api.server import create_app
from egr.cli.commands.integration import app as integration_app
from egr.core.config import EGRConfig, IntegrationsConfig, Settings
from egr.core.errors import ConfigError
from egr.core.ids import new_id
from egr.domain.enums import EventType, IntegrationEventStatus, IntegrationKind, RiskLevel
from egr.domain.integration import AuthConfig, InboundConfig, Integration
from egr.domain.tool import ToolRequest
from egr.integrations import ConnectorService
from egr.integrations.loader import load_integration_dir
from egr.integrations.service import CALL_ACTION
from egr.integrations.transports import Transport, sql_call
from egr.policies.engine import PolicyContext
from egr.runtime.runtime import Runtime
from egr.security.rbac import INTEGRATION_CALL, INTEGRATION_MANAGE, has_permission
from egr.templates import render_workspace


# --------------------------------------------------------------------------- #
# falsos de teste
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status: int = 200, text: str = "{}"):
        self.status_code = status
        self.text = text


class FakeClient:
    """Nenhum teste desta fase precisa de internet."""

    def __init__(self, status: int = 200, text: str = '{"ok": true}', handler=None):
        self.status = status
        self.text = text
        self.handler = handler
        self.requests: list[dict] = []

    def request(self, method, url, headers=None, content=None, json=None, timeout=None):
        self.requests.append(
            {"method": method, "url": url, "headers": headers or {}, "content": content, "json": json}
        )
        if self.handler is not None:
            called = self.handler(method, url, headers or {}, json)
            return called if isinstance(called, FakeResponse) else FakeResponse(200, json.dumps(called or {}))
        if self.status >= 400:
            return FakeResponse(self.status, self.text)
        return FakeResponse(self.status, self.text)


def make_runtime(tmp_path: Path, *, integrations: IntegrationsConfig | None = None) -> Runtime:
    render_workspace(tmp_path, sample=False)
    config = EGRConfig()
    if integrations is not None:
        config.integrations = integrations
    instance = Runtime(Settings(workspace=tmp_path, config=config), enable_logging=False)
    return instance


def connector(
    integration_id: str = "CRM",
    *,
    kind: IntegrationKind = IntegrationKind.REST,
    enabled: bool = True,
    read_only: bool = True,
    base_url: str = "https://api.exemplo.com/v1",
    dsn: str = "",
    methods: list[str] | None = None,
    hosts: list[str] | None = None,
    cost: float = 0.01,
    inbound: InboundConfig | None = None,
    secret: str = "",
    scheme: str = "bearer",
) -> Integration:
    return Integration(
        id=integration_id,
        name=f"Conector {integration_id}",
        type=kind,
        enabled=enabled,
        base_url=base_url,
        dsn=dsn,
        allowed_hosts=hosts if hosts is not None else ["api.exemplo.com"],
        allowed_methods=methods if methods is not None else ["GET"],
        read_only=read_only,
        cost_per_call=cost,
        auth=AuthConfig(scheme=scheme, secret=secret),
        inbound=inbound or InboundConfig(),
    )


# --------------------------------------------------------------------------- #
# domínio e carregamento
# --------------------------------------------------------------------------- #
def test_integration_domain_summary_and_defaults():
    item = connector()

    assert item.kind == "rest"
    assert item.target == "https://api.exemplo.com/v1"
    assert item.allows_method("get") is True
    assert item.allows_method("POST") is False
    resumo = item.summary()
    assert resumo["id"] == "CRM"
    assert resumo["somente_leitura"] is True
    assert resumo["hosts"] == "api.exemplo.com"


def test_integration_read_only_is_the_default():
    item = Integration(id="X", type=IntegrationKind.REST)

    assert item.read_only is True
    assert item.enabled is False
    assert set(item.allowed_methods) == {"GET", "HEAD", "OPTIONS"}


def test_loader_reads_declared_yaml(tmp_path):
    (tmp_path / "integrations").mkdir()
    (tmp_path / "integrations" / "crm.yaml").write_text(
        "integrations:\n"
        "  - id: CRM\n"
        "    type: rest\n"
        "    base_url: https://api.exemplo.com\n"
        "    allowed_hosts: [api.exemplo.com]\n",
        encoding="utf-8",
    )
    (tmp_path / "integrations" / "outro.yml").write_text(
        "id: ERP\ntype: graphql\nbase_url: https://erp.exemplo.com\n", encoding="utf-8"
    )

    found = {item.id: item for item in load_integration_dir(tmp_path / "integrations")}

    assert set(found) == {"CRM", "ERP"}
    assert found["ERP"].kind == "graphql"


def test_loader_missing_directory_is_empty(tmp_path):
    assert load_integration_dir(tmp_path / "nada") == []


def test_loader_rejects_broken_yaml(tmp_path):
    (tmp_path / "integrations").mkdir()
    (tmp_path / "integrations" / "quebrado.yaml").write_text("id: [sem fechar\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_integration_dir(tmp_path / "integrations")


def test_templates_ship_four_disabled_connectors(runtime):
    runtime.sync_integrations()

    kinds = {item.id: item.kind for item in runtime.connectors.list()}

    assert kinds == {"CRM": "rest", "ERP": "graphql", "WAREHOUSE": "sql", "FORNECEDOR": "webhook"}
    assert all(item.enabled is False for item in runtime.connectors.list())


def test_migration_010_applied(runtime):
    assert "010" in set(runtime.migration_state["applied"] or runtime.applied_migrations)
    assert runtime.migration_state["pending"] == []
    assert "010" in runtime.migration_state["applied"]


# --------------------------------------------------------------------------- #
# registro e pré-verificações (antes de sair da máquina)
# --------------------------------------------------------------------------- #
def test_sync_persists_and_lists(runtime):
    runtime.connectors.register(connector("CRM"))

    assert runtime.integrations_repository.count() == 1
    assert [item.id for item in runtime.connectors.load()] == ["CRM"]
    assert runtime.connectors.get("CRM").id == "CRM"


def test_unknown_connector_raises(runtime):
    with pytest.raises(ConfigError):
        runtime.connectors.get("NAO_EXISTE")


def test_disabled_connector_does_not_leave(runtime):
    runtime.connectors.register(connector("CRM", enabled=False))
    runtime.connectors.transport = Transport(client=FakeClient())

    call = runtime.connectors.call("CRM", path="/clientes")

    assert call.ok is False
    assert "desabilitado" in call.error
    assert isinstance(runtime.connectors.transport._client, FakeClient)
    assert runtime.connectors.transport._client.requests == []


def test_method_outside_allowlist_is_refused(runtime):
    runtime.connectors.register(connector("CRM", methods=["GET"], read_only=False))
    runtime.connectors.transport = Transport(client=FakeClient())

    call = runtime.connectors.call("CRM", method="DELETE", path="/clientes/1")

    assert call.ok is False
    assert "fora da lista" in call.error
    assert runtime.connectors.transport._client.requests == []


def test_read_only_connector_refuses_write(runtime):
    runtime.connectors.register(connector("CRM", methods=["GET", "POST"], read_only=True))
    runtime.connectors.transport = Transport(client=FakeClient())

    call = runtime.connectors.call("CRM", method="POST", path="/clientes")

    assert call.ok is False
    assert "somente leitura" in call.error


def test_host_outside_allowlist_is_refused(runtime):
    runtime.connectors.register(connector("CRM", base_url="https://outro.com/v1", hosts=["api.exemplo.com"]))
    runtime.connectors.transport = Transport(client=FakeClient())

    call = runtime.connectors.call("CRM", path="/clientes")

    assert call.ok is False
    assert "lista branca" in call.error


def test_integrations_disabled_by_configuration(tmp_path):
    instance = make_runtime(tmp_path, integrations=IntegrationsConfig(enabled=False))
    instance.connectors.register(connector())

    call = instance.connectors.call("CRM", path="/clientes")

    assert call.ok is False
    assert "desabilitadas" in call.error
    instance.close()


# --------------------------------------------------------------------------- #
# política: leitura passa, escrita pede gente
# --------------------------------------------------------------------------- #
def test_policy_allows_read_and_requires_approval_to_write():
    runtime = make_runtime(Path("/tmp/egr-policy-check"))

    def decide(method: str, environment: str = "development") -> str:
        request = ToolRequest(
            tool="integration.rest",
            action=CALL_ACTION,
            args={"method": method, "operation": method, "write": method not in ("GET", "HEAD")},
        )
        context = PolicyContext(environment=environment, risk=RiskLevel.LOW)
        return str(runtime.policy.evaluate(request, context).decision)

    assert decide("GET") == "allow"
    assert decide("POST") == "require_approval"
    assert decide("POST", environment="production") == "require_approval"
    runtime.close()


def test_read_call_executes_and_is_recorded(runtime):
    runtime.connectors.register(connector("CRM"))
    client = FakeClient(text='{"clientes": [{"id": 1}]}')
    runtime.connectors.transport = Transport(client=client)

    call = runtime.connectors.call("CRM", path="/clientes", actor="human:cli")

    assert call.ok is True
    assert call.status == 200
    assert call.decision == "integration-read"
    assert call.cost == 0.01
    assert client.requests[0]["url"] == "https://api.exemplo.com/v1/clientes"
    assert runtime.integration_calls.count() == 1
    assert runtime.integration_calls.list(limit=1)[0].id == call.id


def test_write_call_without_preauthorization_is_recorded_as_denied(runtime):
    runtime.connectors.register(connector("CRM", methods=["GET", "POST"], read_only=False))
    runtime.connectors.transport = Transport(client=FakeClient())

    call = runtime.connectors.call("CRM", method="POST", path="/clientes", body='{"nome":"Ana"}')

    assert call.ok is False
    assert "aprovação necessária" in call.error
    assert call.approved is False
    assert runtime.connectors.transport._client.requests == []


def test_write_call_preauthorized_executes(runtime):
    runtime.connectors.register(connector("CRM", methods=["GET", "POST"], read_only=False))
    client = FakeClient(text='{"id": 9}')
    runtime.connectors.transport = Transport(client=client)

    call = runtime.connectors.call(
        "CRM", method="POST", path="/clientes", body='{"nome":"Ana"}', preauthorized=True, task_id="tsk_1"
    )

    assert call.ok is True
    assert "autorizado no plano" in call.decision
    assert client.requests[0]["method"] == "POST"
    assert call.task_id == "tsk_1"


def test_secret_never_lands_in_the_trail(runtime):
    runtime.connectors.register(connector("CRM", secret="api-KEYTESTE1234567890"))
    client = FakeClient(text='{"token": "sk-segredo1234567890", "ok": true}')
    runtime.connectors.transport = Transport(client=client)

    call = runtime.connectors.call("CRM", path="/clientes")

    assert "sk-segredo1234567890" not in call.response_summary
    assert "redacted" in call.response_summary
    assert "Bearer" not in call.request_summary


def test_credential_is_resolved_from_environment_not_yaml(runtime, monkeypatch):
    monkeypatch.setenv("CRM_TOKEN", "tok-abc")
    runtime.connectors.register(connector("CRM", secret="CRM_TOKEN", scheme="bearer"))
    client = FakeClient()
    runtime.connectors.transport = Transport(client=client)

    runtime.connectors.call("CRM", path="/clientes")

    assert client.requests[0]["headers"]["Authorization"] == "Bearer tok-abc"


def test_dry_run_does_not_leave_the_machine(runtime):
    runtime.connectors.register(connector("CRM"))
    client = FakeClient()
    runtime.connectors.transport = Transport(client=client)

    call = runtime.connectors.call("CRM", path="/clientes", dry_run=True)

    assert call.ok is True
    assert client.requests == []


def test_transport_failure_is_measured_not_raised(runtime):
    runtime.connectors.register(connector("CRM"))

    class Broken:
        def request(self, *args, **kwargs):
            raise RuntimeError("sem rede")

    runtime.connectors.transport = Transport(client=Broken())

    call = runtime.connectors.call("CRM", path="/clientes")

    assert call.ok is False
    assert "sem rede" in call.error


def test_stats_group_calls_by_connector(runtime):
    runtime.connectors.register(connector("CRM", cost=0.5))
    runtime.connectors.transport = Transport(client=FakeClient())
    runtime.connectors.call("CRM", path="/a")
    runtime.connectors.call("CRM", path="/b")

    stats = runtime.integration_calls.stats()

    assert stats["CRM"]["total"] == 2
    assert stats["CRM"]["sucesso"] == 2
    assert stats["CRM"]["custo"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# GraphQL e SQL
# --------------------------------------------------------------------------- #
def test_graphql_call_sends_query_and_variables(runtime):
    runtime.connectors.register(
        connector(
            "ERP",
            kind=IntegrationKind.GRAPHQL,
            base_url="https://erp.exemplo.com/graphql",
            methods=["POST"],
            hosts=["erp.exemplo.com"],
        )
    )
    client = FakeClient(text='{"data": {"estoque": 3}}')
    runtime.connectors.transport = Transport(client=client)

    call = runtime.connectors.call("ERP", method="POST", query="query($id: ID!){ estoque(id: $id) }",
                                   variables={"id": "1"})

    assert call.ok is True
    assert client.requests[0]["json"] == {
        "query": "query($id: ID!){ estoque(id: $id) }",
        "variables": {"id": "1"},
    }


def test_graphql_errors_are_failures(runtime):
    runtime.connectors.register(
        connector(
            "ERP",
            kind=IntegrationKind.GRAPHQL,
            base_url="https://erp.exemplo.com/graphql",
            methods=["POST"],
            hosts=["erp.exemplo.com"],
        )
    )
    client = FakeClient(text='{"errors": [{"message": "não autorizado"}]}')
    runtime.connectors.transport = Transport(client=client)

    call = runtime.connectors.call("ERP", method="POST", query="{ __typename }")

    assert call.ok is False
    assert call.error == "não autorizado"


def test_sql_reads_declared_sqlite(tmp_path):
    db = tmp_path / "dados.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE notas (id INTEGER, valor REAL)")
    connection.execute("INSERT INTO notas VALUES (1, 10.5)")
    connection.commit()
    connection.close()

    runtime = make_runtime(tmp_path / "ws")
    runtime.connectors.register(
        connector("WAREHOUSE", kind=IntegrationKind.SQL, dsn=f"sqlite:///{db}", base_url="")
    )

    call = runtime.connectors.call("WAREHOUSE", query="select id, valor from notas")

    assert call.ok is True
    assert "10.5" in (call.response_summary or "")
    runtime.close()


def test_sql_write_on_read_only_connector_is_refused(tmp_path):
    runtime = make_runtime(tmp_path / "ws")
    runtime.connectors.register(
        connector("WAREHOUSE", kind=IntegrationKind.SQL, dsn="sqlite:///:memory:", base_url="")
    )

    call = runtime.connectors.call("WAREHOUSE", query="delete from notas")

    assert call.ok is False
    assert "somente leitura" in call.error
    runtime.close()


def test_sql_driver_must_be_declared(tmp_path):
    runtime = make_runtime(tmp_path / "ws", integrations=IntegrationsConfig(allow_sql_drivers=["postgres"]))
    runtime.connectors.register(
        connector("WAREHOUSE", kind=IntegrationKind.SQL, dsn="sqlite:///:memory:", base_url="")
    )

    call = runtime.connectors.call("WAREHOUSE", query="select 1 as ok")

    assert call.ok is False
    assert "sqlite não está entre os drivers" in call.error
    runtime.close()


def test_sql_call_rejects_unknown_driver():
    result = sql_call(dsn="postgres://localhost/db", statement="select 1")

    assert result["ok"] is False
    assert "driver" in result["error"]


# --------------------------------------------------------------------------- #
# entrada: webhook assinado e idempotente
# --------------------------------------------------------------------------- #
def signed_event(runtime, integration_id: str, payload: dict, *, secret: str = "segredo", event_id: str = "evt_1"):
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    signature = ConnectorService.sign(secret, body)
    return runtime.connectors.receive(
        integration_id,
        payload,
        headers={
            "x-egr-signature": signature,
            "x-egr-event-id": event_id,
        },
    )


def webhook_connector(integration_id: str = "FORNECEDOR", *, secret: str = "WEBHOOK", enabled: bool = True,
                      event_type: str = "integration.fornecedor"):
    return connector(
        integration_id,
        kind=IntegrationKind.WEBHOOK,
        enabled=enabled,
        inbound=InboundConfig(enabled=True, secret=secret, event_type=event_type),
    )


def test_inbound_event_signed_is_accepted(runtime, monkeypatch):
    monkeypatch.setenv("WEBHOOK", "segredo")
    runtime.connectors.register(webhook_connector())

    event = signed_event(runtime, "FORNECEDOR", {"id": "evt_1", "pedido": 42})

    assert str(event.status) == "received"
    assert event.signature_ok is True
    assert runtime.integration_events.count() == 1


def test_inbound_event_with_bad_signature_is_rejected(runtime, monkeypatch):
    monkeypatch.setenv("WEBHOOK", "segredo")
    runtime.connectors.register(webhook_connector())

    event = runtime.connectors.receive(
        "FORNECEDOR",
        {"id": "evt_1"},
        headers={"x-egr-signature": "t=1,v1=errado", "x-egr-event-id": "evt_1"},
    )

    assert str(event.status) == "rejected"
    assert "assinatura" in event.error


def test_inbound_event_replay_outside_window_is_rejected(runtime, monkeypatch):
    monkeypatch.setenv("WEBHOOK", "segredo")
    runtime.connectors.register(webhook_connector())
    body = json.dumps({"id": "evt_antigo"}, separators=(",", ":"), sort_keys=True)
    old = ConnectorService.sign("segredo", body, timestamp=1)

    event = runtime.connectors.receive(
        "FORNECEDOR", {"id": "evt_antigo"}, headers={"x-egr-signature": old, "x-egr-event-id": "evt_antigo"}
    )

    assert str(event.status) == "rejected"


def test_inbound_event_is_idempotent(runtime, monkeypatch):
    monkeypatch.setenv("WEBHOOK", "segredo")
    runtime.connectors.register(webhook_connector())

    first = signed_event(runtime, "FORNECEDOR", {"id": "evt_1", "pedido": 1}, event_id="evt_1")
    second = signed_event(runtime, "FORNECEDOR", {"id": "evt_1", "pedido": 1}, event_id="evt_1")

    assert str(first.status) == "received"
    assert str(second.status) == "duplicate"
    assert runtime.integration_events.count() == 1


def test_inbound_event_from_disabled_connector_is_rejected(runtime):
    runtime.connectors.register(webhook_connector(enabled=False))

    event = runtime.connectors.receive("FORNECEDOR", {"id": "evt_9"}, headers={"x-egr-event-id": "evt_9"})

    assert str(event.status) == "rejected"
    assert "desabilitado" in event.error


def test_inbound_event_publishes_to_the_bus(runtime, monkeypatch):
    monkeypatch.setenv("WEBHOOK", "segredo")
    runtime.connectors.register(webhook_connector())

    signed_event(runtime, "FORNECEDOR", {"id": "evt_1"}, event_id="evt_1")

    tipos = [str(event.type) for event in runtime.audit.list(limit=200)]
    assert EventType.INTEGRATION_EVENT_RECEIVED in tipos


def test_inbound_event_with_declared_trigger_is_marked_triggered(runtime, monkeypatch):
    from egr.domain.workflow import Workflow

    monkeypatch.setenv("WEBHOOK", "segredo")
    runtime.connectors.register(webhook_connector())
    workflow = Workflow(
        id="recebe-fornecedor",
        name="Recebe evento do fornecedor",
        steps=[],
        trigger={"type": "event", "event": "integration.*", "enabled": True},
    )
    runtime.workflows[workflow.id] = workflow

    event = signed_event(runtime, "FORNECEDOR", {"id": "evt_1"}, event_id="evt_1")

    assert event.status == IntegrationEventStatus.TRIGGERED


# --------------------------------------------------------------------------- #
# trilha e auditoria
# --------------------------------------------------------------------------- #
def test_calls_and_denials_are_audited(runtime):
    runtime.connectors.register(connector("CRM"))
    runtime.connectors.transport = Transport(client=FakeClient())
    runtime.connectors.call("CRM", path="/ok")
    runtime.connectors.call("CRM", method="DELETE", path="/nope")

    tipos = [str(event.type) for event in runtime.audit.list(limit=200)]

    assert EventType.INTEGRATION_CALLED in tipos
    assert EventType.INTEGRATION_DENIED in tipos


def test_status_reports_connectors_calls_and_events(runtime):
    runtime.connectors.register(connector("CRM"))
    runtime.connectors.transport = Transport(client=FakeClient())
    runtime.connectors.call("CRM", path="/clientes")

    status = runtime.integrations_status()

    assert status["conectores"]["total"] == 1
    assert status["chamadas"]["total"] == 1
    assert status["chamadas"]["recentes"][0]["conector"] == "CRM"
    assert status["habilitado"] is True


def test_health_flags_connector_without_host_allowlist(runtime):
    runtime.connectors.register(connector("CRM", hosts=[]))

    checks = {item["check"]: item for item in runtime.health()["checks"]}

    assert "integrations" in checks
    assert checks["integrations"]["ok"] is True
    assert checks["integracao:CRM"]["ok"] is False


def test_status_includes_integrations(runtime):
    assert "integrations" in runtime.status()


# --------------------------------------------------------------------------- #
# ferramenta (o agente usa o conector, não a URL)
# --------------------------------------------------------------------------- #
def test_integration_tools_are_registered(runtime):
    names = {tool["name"] for tool in runtime.tools.list()}

    assert "integration.call" in names
    assert "integration.list" in names


def test_integration_call_tool_uses_declared_connector(runtime):
    from egr.tools.protocol import ToolContext

    runtime.connectors.register(connector("CRM", methods=["GET", "POST"], read_only=False))
    client = FakeClient(text='{"id": 7}')
    runtime.connectors.transport = Transport(client=client)
    ctx = ToolContext(
        workspace=runtime.settings.workspace,
        sandbox=runtime.settings.sandbox_path,
        artifacts=runtime.settings.artifacts_path,
        runtime=runtime,
        agent_id="ops-agent",
        task_id="tsk_99",
    )

    result = runtime.tools.execute(
        "integration.call",
        {"integration": "CRM", "method": "POST", "path": "/clientes", "body": '{"nome":"Ana"}'},
        ctx,
    )

    assert result.ok is True
    assert result.output["status"] == 200
    assert result.output["conector"] == "CRM"
    assert runtime.integration_calls.list(limit=1)[0].actor == "ops-agent"


def test_integration_call_tool_dry_run_does_not_execute(runtime):
    from egr.tools.protocol import ToolContext

    runtime.connectors.register(connector("CRM", enabled=False))
    ctx = ToolContext(
        workspace=runtime.settings.workspace,
        sandbox=runtime.settings.sandbox_path,
        artifacts=runtime.settings.artifacts_path,
        runtime=runtime,
        dry_run=True,
    )

    result = runtime.tools.execute("integration.call", {"integration": "CRM", "path": "/clientes"}, ctx)

    assert result.ok is True
    assert result.output["dry_run"] is True
    assert runtime.integration_calls.count() == 0


def test_integration_call_tool_fails_without_connector(runtime):
    from egr.tools.protocol import ToolContext

    ctx = ToolContext(
        workspace=runtime.settings.workspace,
        sandbox=runtime.settings.sandbox_path,
        artifacts=runtime.settings.artifacts_path,
        runtime=runtime,
    )

    result = runtime.tools.execute("integration.call", {"integration": "NAO_EXISTE"}, ctx)

    assert result.ok is False
    assert "não encontrado" in result.error


def test_integration_list_tool(runtime):
    from egr.tools.protocol import ToolContext

    runtime.connectors.register(connector("CRM"))
    ctx = ToolContext(
        workspace=runtime.settings.workspace,
        sandbox=runtime.settings.sandbox_path,
        artifacts=runtime.settings.artifacts_path,
        runtime=runtime,
    )

    result = runtime.tools.execute("integration.list", {}, ctx)

    assert result.output["total"] == 1
    assert result.output["conectores"][0]["id"] == "CRM"


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #
def test_rbac_integration_permissions():
    assert has_permission("operator", INTEGRATION_CALL) is True
    assert has_permission("viewer", INTEGRATION_CALL) is False
    assert has_permission("security_admin", INTEGRATION_MANAGE) is True
    assert has_permission("operator", INTEGRATION_MANAGE) is False


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def test_api_lists_and_calls_connectors(runtime):
    runtime.connectors.register(connector("CRM"))
    runtime.connectors.transport = Transport(client=FakeClient(text='{"ok": true}'))
    client = TestClient(create_app(runtime))

    assert client.get("/v1/integrations").json()["conectores"]["total"] == 1
    call = client.post("/v1/integrations/CRM/call", json={"method": "GET", "path": "/clientes"}).json()

    assert call["ok"] is True
    assert call["decisão"] == "integration-read"
    assert client.get("/v1/integrations/calls").json()[0]["conector"] == "CRM"


def test_api_receives_signed_webhook(runtime, monkeypatch):
    monkeypatch.setenv("WEBHOOK", "segredo")
    runtime.connectors.register(webhook_connector())
    client = TestClient(create_app(runtime))
    payload = {"id": "evt_api", "pedido": 3}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    signature = ConnectorService.sign("segredo", body)

    response = client.post(
        "/v1/integrations/FORNECEDOR/events",
        content=body,
        headers={
            "content-type": "application/json",
            "x-egr-signature": signature,
            "x-egr-event-id": "evt_api",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert client.get("/v1/integrations/events").json()[0]["conector"] == "FORNECEDOR"


def test_api_rejects_webhook_without_signature(runtime, monkeypatch):
    monkeypatch.setenv("WEBHOOK", "segredo")
    runtime.connectors.register(webhook_connector())
    client = TestClient(create_app(runtime))

    response = client.post("/v1/integrations/FORNECEDOR/events", json={"id": "evt_x"})

    assert response.status_code == 401


def test_api_enables_and_disables_connector(runtime):
    runtime.connectors.register(connector("CRM", enabled=False))
    client = TestClient(create_app(runtime))

    assert client.post("/v1/integrations/CRM/enable").json()["habilitado"] is True
    assert client.post("/v1/integrations/CRM/enable?disable=true").json()["habilitado"] is False


def test_api_unknown_connector_is_404(runtime):
    client = TestClient(create_app(runtime))

    assert client.get("/v1/integrations/NAO_EXISTE").status_code == 404


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_list_and_calls(tmp_path):
    runtime = make_runtime(tmp_path)
    render_workspace(tmp_path, sample=False)
    runtime.sync_integrations()
    runner = CliRunner()

    listed = runner.invoke(integration_app, ["list", "-w", str(tmp_path)])

    assert listed.exit_code == 0, listed.output
    assert "CRM" in listed.output

    calls = runner.invoke(integration_app, ["calls", "-w", str(tmp_path)])

    assert calls.exit_code == 0
    runtime.close()


def test_cli_call_denied_shows_the_reason(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.connectors.register(connector("CRM", enabled=False))
    runner = CliRunner()

    result = runner.invoke(integration_app, ["call", "CRM", "/clientes", "-w", str(tmp_path)])

    assert result.exit_code == 1
    assert "recusada" in result.output
    runtime.close()


def test_cli_sync_lists_declared_connectors(tmp_path):
    runtime = make_runtime(tmp_path)
    render_workspace(tmp_path, sample=False)
    runner = CliRunner()

    result = runner.invoke(integration_app, ["sync", "-w", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "4" in result.output
    runtime.close()


def test_cli_events_reports_inbound(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    monkeypatch.setenv("WEBHOOK", "segredo")
    runtime.connectors.register(webhook_connector())
    signed_event(runtime, "FORNECEDOR", {"id": "evt_cli"}, event_id="evt_cli")
    runner = CliRunner()

    result = runner.invoke(integration_app, ["events", "-w", str(tmp_path)])

    assert result.exit_code == 0
    assert "received" in result.output
    runtime.close()


# --------------------------------------------------------------------------- #
# serviço isolado
# --------------------------------------------------------------------------- #
def test_connector_service_can_be_built_without_runtime(tmp_path):
    render_workspace(tmp_path, sample=False)
    runtime = Runtime(Settings(workspace=tmp_path, config=EGRConfig()), enable_logging=False)
    service = ConnectorService(runtime)

    assert service.list() == []
    assert service.status()["conectores"]["total"] == 0
    runtime.close()


def test_call_ids_are_unique(runtime):
    runtime.connectors.register(connector("CRM"))
    runtime.connectors.transport = Transport(client=FakeClient())

    first = runtime.connectors.call("CRM", path="/a")
    second = runtime.connectors.call("CRM", path="/b")

    assert first.id != second.id
    assert first.id.startswith("cal_")
    assert new_id("call") != first.id
