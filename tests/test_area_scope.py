"""Limite de acesso por área: o agente do financeiro não alcança o RH.

Fatia 1 do fechamento do runtime: `permissions.namespaces` (que os packs já
declaravam) agora é imposto de verdade na memória. O overseer usa `"*"`.
Toda negação cai na trilha.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from egr.cli.main import app as cli_app
from egr.core.errors import AuthorizationError
from egr.core.ids import new_id
from egr.domain.agent import AgentPermissions, AgentSpec
from egr.domain.approval import Approval, ApprovalStatus
from egr.domain.enums import Environment, TaskStatus
from egr.packs.catalog import builtin_packs
from egr.security.identity import Principal
from egr.security.rbac import in_area

DENIED = "security.authorization_denied"


def _seed(runtime) -> None:
    runtime.memory.write("reajuste salarial de 5% aprovado", namespace="hr")
    runtime.memory.write("fechamento do Q3 concluído", namespace="finance")


def test_escrita_fora_do_escopo_e_negada_com_trilha(runtime):
    _seed(runtime)
    with pytest.raises(AuthorizationError, match="fora do escopo"):
        runtime.memory.write(
            "x", namespace="hr", allowed_namespaces=["finance"], agent_id="finance-agent"
        )

    denials = runtime.audit.list(type=DENIED, limit=10)
    assert denials
    assert denials[-1].payload["op"] == "memory.write"
    assert denials[-1].payload["requested"] == ["hr"]


def test_recall_fora_do_escopo_e_negado(runtime):
    _seed(runtime)
    with pytest.raises(AuthorizationError, match="fora do escopo"):
        runtime.memory.recall(
            "reajuste", namespaces=["hr"], allowed_namespaces=["finance"], agent_id="finance-agent"
        )


def test_busca_sem_namespace_estreita_para_o_escopo(runtime):
    _seed(runtime)
    records = runtime.memory.search(
        "reajuste fechamento Q3", namespaces=None, allowed_namespaces=["finance"]
    )

    assert records
    assert {record.namespace for record in records} == {"finance"}


def test_overseer_com_coringa_alcanca_tudo(runtime):
    _seed(runtime)
    records, _ = runtime.memory.recall("reajuste", namespaces=["hr"], allowed_namespaces=["*"])
    assert records
    runtime.memory.write("anotação geral", namespace="hr", allowed_namespaces=["*"])


def test_sem_escopo_declarado_mantem_o_comportamento(runtime):
    _seed(runtime)
    records, _ = runtime.memory.recall("reajuste", namespaces=["hr"])
    assert records


def test_motor_falha_fechado_quando_agente_estoura_o_escopo(runtime):
    runtime.agents["sneaky-agent"] = AgentSpec(
        id="sneaky-agent",
        memory=["hr"],
        permissions=AgentPermissions(tools=["*"], namespaces=["finance"]),
    )

    task = runtime.submit("liste os segredos do RH", agent_id="sneaky-agent")

    assert task.status == TaskStatus.FAILED
    assert "fora do escopo" in (task.error or "")


def test_packs_builtin_declaram_escopo_consistente():
    packs = builtin_packs()
    assert len(packs) >= 7
    for pack in packs:
        for agent in pack.agents:
            namespaces = (agent.get("permissions") or {}).get("namespaces") or []
            if namespaces and "*" not in namespaces:
                outside = [ns for ns in agent.get("memory") or [] if ns not in namespaces]
                assert not outside, f"{pack.id}/{agent.get('id')}: {outside}"


# ---- fatia 2: áreas para pessoas ------------------------------------------


def _approval(runtime, agent_id: str) -> str:
    task = runtime.task_engine.create(f"rotina de {agent_id}", agent_id=agent_id, created_by="cli")
    approval = Approval(
        id=new_id("ap"),
        action="execute",
        tool="database.query",
        requested_by=agent_id,
        task_id=task.id,
        environment=Environment.PRODUCTION,
        status=ApprovalStatus.PENDING,
        reason="teste de área",
    )
    runtime.approvals.save(approval)
    return approval.id


def test_in_area_sem_area_no_recurso_libera_todos():
    principal = Principal(id="x", areas=["hr"], roles=["viewer"])

    assert in_area(principal, None)
    assert in_area(principal, "")


def test_in_area_sem_grants_alcanca_tudo():
    assert in_area(Principal(id="x", roles=["viewer"]), "finance")


def test_in_area_coringa_e_admin_sao_overseer():
    assert in_area(Principal(id="x", areas=["*"], roles=["viewer"]), "finance")
    assert in_area(Principal(id="x", roles=["admin"]), "finance")


def test_in_area_barra_fora_da_area():
    principal = Principal(id="x", areas=["hr"], roles=["viewer"])

    assert in_area(principal, "hr")
    assert not in_area(principal, "finance")


def test_principal_guarda_areas(runtime):
    created = runtime.identity.create_principal("gerente-rh", areas=["hr"])

    assert created.areas == ["hr"]
    assert runtime.identity.get("gerente-rh").areas == ["hr"]

    updated = runtime.identity.set_areas("gerente-rh", ["hr", "finance"])
    assert updated.areas == ["finance", "hr"]


def test_aprovacao_entre_areas_diferentes_e_negada(runtime):
    runtime.identity.create_principal("gerente-rh", areas=["hr"])
    approval_id = _approval(runtime, "finance-agent")

    with pytest.raises(AuthorizationError, match="não alcança a área"):
        runtime.approve(approval_id, decided_by="gerente-rh")

    denials = runtime.audit.list(type=DENIED, limit=10)
    assert denials and "não alcança a área" in denials[-1].payload["reason"]


def test_aprovacao_na_mesma_area_passa(runtime):
    runtime.identity.create_principal("gerente-fin", areas=["finance"])
    approval_id = _approval(runtime, "finance-agent")

    runtime.approve(approval_id, decided_by="gerente-fin")

    assert runtime.approvals.get(approval_id).status == "approved"


def test_aprovacao_em_agente_global_ignora_area(runtime):
    runtime.identity.create_principal("gerente-rh", areas=["hr"])
    approval_id = _approval(runtime, "runtime-agent")

    runtime.approve(approval_id, decided_by="gerente-rh")

    assert runtime.approvals.get(approval_id).status == "approved"


def test_decisor_desconhecido_mantem_comportamento(runtime):
    approval_id = _approval(runtime, "finance-agent")

    runtime.approve(approval_id, decided_by="fulano")

    assert runtime.approvals.get(approval_id).status == "approved"


def test_identity_add_com_areas_e_comando_areas(workspace):
    runner = CliRunner()
    args = ["--workspace", str(workspace)]
    added = runner.invoke(cli_app, ["identity", "add", "gerente-rh", "--areas", "hr,finance", *args])
    assert added.exit_code == 0, added.output

    shown = runner.invoke(cli_app, ["identity", "show", "gerente-rh", *args])
    assert shown.exit_code == 0
    assert "finance" in shown.output and "hr" in shown.output

    changed = runner.invoke(cli_app, ["identity", "areas", "gerente-rh", "--areas", "hr", *args])
    assert changed.exit_code == 0, changed.output

    shown_again = runner.invoke(cli_app, ["identity", "show", "gerente-rh", "--json", *args])
    assert json.loads(shown_again.output)["areas"] == ["hr"]


def test_memory_search_com_by_respeita_area(workspace):
    runner = CliRunner()
    args = ["--workspace", str(workspace)]
    assert runner.invoke(cli_app, ["identity", "add", "gerente-rh", "--areas", "hr", *args]).exit_code == 0
    assert (
        runner.invoke(cli_app, ["memory", "write", "reajuste salarial aprovado", "-n", "hr", *args]).exit_code
        == 0
    )
    assert (
        runner.invoke(cli_app, ["memory", "write", "fechamento do trimestre", "-n", "finance", *args]).exit_code
        == 0
    )

    scoped = runner.invoke(
        cli_app, ["memory", "search", "reajuste fechamento", "--by", "gerente-rh", "--json", *args]
    )
    assert scoped.exit_code == 0, scoped.output
    assert {record["namespace"] for record in json.loads(scoped.output)} == {"hr"}

    denied = runner.invoke(
        cli_app, ["memory", "search", "fechamento", "-n", "finance", "--by", "gerente-rh", *args]
    )
    assert denied.exit_code == 1
    assert "fora do escopo" in denied.output

    free = runner.invoke(cli_app, ["memory", "search", "reajuste fechamento", "--json", *args])
    assert free.exit_code == 0
    assert {record["namespace"] for record in json.loads(free.output)} == {"hr", "finance"}


def test_memory_write_com_by_fora_da_area_e_negado(workspace):
    runner = CliRunner()
    args = ["--workspace", str(workspace)]
    assert runner.invoke(cli_app, ["identity", "add", "gerente-rh", "--areas", "hr", *args]).exit_code == 0

    result = runner.invoke(
        cli_app, ["memory", "write", "x", "-n", "finance", "--by", "gerente-rh", *args]
    )
    assert result.exit_code == 1
    assert "fora do escopo" in result.output


def test_packs_declaram_area_do_setor():
    for pack in builtin_packs():
        assert pack.agents, f"{pack.id} sem agentes"
        for agent in pack.agents:
            assert agent.get("area") == pack.id
            AgentSpec.model_validate(agent)
