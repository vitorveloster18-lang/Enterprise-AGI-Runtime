"""Limite de acesso por área: o agente do financeiro não alcança o RH.

Fatia 1 do fechamento do runtime: `permissions.namespaces` (que os packs já
declaravam) agora é imposto de verdade na memória. O overseer usa `"*"`.
Toda negação cai na trilha.
"""

from __future__ import annotations

import pytest

from egr.core.errors import AuthorizationError
from egr.domain.agent import AgentPermissions, AgentSpec
from egr.domain.enums import TaskStatus
from egr.packs.catalog import builtin_packs

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
