"""Supervisão formal: o orquestrador delega, revisa e escala ao humano.

Fatia 3 do fechamento do runtime: `task.delegate` cria a filha ligada
(`parent_id`), executa pelo motor governado, revisa a entrega
(accept/revise/escalate) e escala ao humano da ÁREA DA FILHA quando é
crítico — sem retomada automática (veredito, não pausa).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from egr.core.errors import AuthorizationError
from egr.domain.enums import TaskStatus
from egr.domain.tool import ToolRequest
from egr.runtime.loader import load_agent_file
from egr.tools.builtins.delegate import DelegateTool, parse_verdict

DELEGATED = "task.delegated"
REVIEW = "supervision.review"
RECALLED = "memory.recalled"


def _ctx(runtime, task_id=None, agent_id="orchestrator-agent", environment="development"):
    agent = runtime.agents.get(agent_id)
    return runtime.adhoc_tool_context(environment, task_id=task_id, agent=agent)


def _delegate(runtime, monkeypatch, verdicts, agent_id="runtime-agent", args=None, **kwargs):
    """Executa task.delegate com a revisão dublada (Echo não dá vereditos)."""
    answers = list(verdicts)

    def fake_review(self, rt, reviewer, child, criteria, on_unclear="escalate"):
        verdict = answers.pop(0) if len(answers) > 1 else answers[0]
        return verdict, f"notas: {verdict}"

    monkeypatch.setattr(DelegateTool, "review_child", fake_review)
    parent = runtime.task_engine.create("meta do orquestrador", "orchestrator-agent")
    ctx = _ctx(runtime, task_id=parent.id, **kwargs)
    result = runtime.tools.execute(
        "task.delegate",
        {"agent_id": agent_id, "objective": "Listar documentos", **(args or {})},
        ctx,
    )
    return result, parent


def test_parse_verdict_aceita_json_pt_en_e_linha():
    assert parse_verdict('{"veredito": "aceito", "notas": "ok"}') == ("accept", "ok")
    assert parse_verdict('{"verdict": "revise", "notes": "x"}') == ("revise", "x")
    assert parse_verdict('```json\n{"veredito": "escalate"}\n```')[0] == "escalate"
    assert parse_verdict("análise feita.\nVEREDITO: revisAR")[0] == "revise"
    assert parse_verdict("texto sem veredito")[0] == "escalate"
    assert parse_verdict("texto sem veredito", on_unclear="accept")[0] == "accept"


def test_delegate_aceita_filha_e_liga_parent(runtime, monkeypatch):
    result, parent = _delegate(runtime, monkeypatch, ["accept"])

    assert result.ok
    assert result.output["status"] == "accepted"
    assert result.output["rounds"] == 0
    child = runtime.tasks.get(result.output["child_id"])
    assert child.parent_id == parent.id
    assert child.context["delegate_depth"] == 1
    assert runtime.audit.list(type=DELEGATED, limit=5)
    assert runtime.audit.list(type=REVIEW, limit=5)


def test_revise_repete_filha_com_feedback(runtime, monkeypatch):
    result, parent = _delegate(runtime, monkeypatch, ["revise", "accept"], args={"max_rounds": 2})

    assert result.ok
    assert result.output["status"] == "accepted"
    assert result.output["rounds"] == 1
    children = [t for t in runtime.task_engine.list(limit=50) if t.parent_id == parent.id]
    assert len(children) == 2
    com_feedback = [c for c in children if "Revisão anterior" in c.objective]
    assert len(com_feedback) == 1


def test_rounds_esgotados_escalam_para_area_da_filha(runtime, monkeypatch):
    result, _ = _delegate(runtime, monkeypatch, ["revise"], agent_id="finance-agent", args={"max_rounds": 1})

    assert result.ok
    assert result.output["status"] == "escalated"
    approval = runtime.approvals.get(result.output["approval_id"])
    assert approval.tool == "task.delegate"
    assert approval.action == "review"
    assert approval.task_id == result.output["child_id"]


def test_veredito_escalate_abre_aprovacao(runtime, monkeypatch):
    result, _ = _delegate(runtime, monkeypatch, ["escalate"])

    assert result.ok
    assert result.output["status"] == "escalated"
    approval = runtime.approvals.get(result.output["approval_id"])
    assert approval.status == "pending"


def test_escalacao_decidida_na_area_nao_retoma_filha(runtime, monkeypatch):
    runtime.identity.create_principal("gerente-fin", areas=["finance"])
    result, _ = _delegate(runtime, monkeypatch, ["escalate"], agent_id="finance-agent")
    child_before = runtime.tasks.get(result.output["child_id"])
    steps_before = len(child_before.result.steps)

    runtime.approve(result.output["approval_id"], decided_by="gerente-fin")

    approval = runtime.approvals.get(result.output["approval_id"])
    assert approval.status == "approved"
    child_after = runtime.tasks.get(result.output["child_id"])
    assert child_after.status == TaskStatus.COMPLETED
    assert len(child_after.result.steps) == steps_before


def test_escalacao_entre_areas_negada(runtime, monkeypatch):
    runtime.identity.create_principal("gerente-rh", areas=["hr"])
    result, _ = _delegate(runtime, monkeypatch, ["escalate"], agent_id="finance-agent")

    with pytest.raises(AuthorizationError, match="não alcança a área"):
        runtime.approve(result.output["approval_id"], decided_by="gerente-rh")


def test_filha_pausada_devolve_waiting_approval(runtime, monkeypatch):
    monkeypatch.setattr(DelegateTool, "review_child", lambda self, *a, **k: ("accept", "ok"))
    parent = runtime.task_engine.create("meta do orquestrador", "orchestrator-agent")
    ctx = _ctx(runtime, task_id=parent.id, environment="production")
    result = runtime.tools.execute(
        "task.delegate",
        {"agent_id": "runtime-agent", "objective": "Gerar relatório consolidado"},
        ctx,
    )

    assert result.ok
    assert result.output["status"] == "waiting_approval"
    approval = runtime.approvals.get(result.output["approval_id"])
    assert approval.status == "pending"
    assert approval.task_id == result.output["child_id"]


def test_profundidade_maxima_bloqueia(runtime, monkeypatch):
    parent = runtime.task_engine.create("meta profunda", "orchestrator-agent")
    parent.context["delegate_depth"] = 2
    runtime.tasks.save(parent)
    ctx = _ctx(runtime, task_id=parent.id)
    result = runtime.tools.execute("task.delegate", {"agent_id": "runtime-agent", "objective": "x"}, ctx)

    assert not result.ok
    assert "profundidade máxima" in result.error


def test_agente_desconhecido_e_sem_runtime_falham(runtime):
    ctx = _ctx(runtime)
    result = runtime.tools.execute("task.delegate", {"agent_id": "fantasma", "objective": "x"}, ctx)
    assert not result.ok
    assert "unknown agent" in result.error

    bare = _ctx(runtime)
    bare.runtime = None
    result = runtime.tools.execute("task.delegate", {"agent_id": "runtime-agent", "objective": "x"}, bare)
    assert not result.ok
    assert "exige contexto do Runtime" in result.error


def test_allowlist_controla_quem_delega(runtime):
    sem_orquestra = runtime.agents["runtime-agent"]
    decision = runtime.authorize(
        ToolRequest(tool="task.delegate", args={}, agent_id=sem_orquestra.id),
        sem_orquestra,
    )
    assert str(decision.decision) == "deny"

    orquestrador = runtime.agents["orchestrator-agent"]
    decision = runtime.authorize(
        ToolRequest(tool="task.delegate", args={}, agent_id=orquestrador.id),
        orquestrador,
    )
    assert str(decision.decision) == "allow"


def test_overseer_recorda_todas_as_areas(runtime):
    hr = runtime.memory.write("pendências do RH: reajuste salarial de 5%", namespace="hr")
    fin = runtime.memory.write("pendências do financeiro: fechamento do Q3", namespace="finance")
    task = runtime.task_engine.submit("Consolidar pendências das áreas", agent_id="orchestrator-agent")

    events = runtime.audit.list(type=RECALLED, limit=50)
    mine = [e for e in events if e.task_id == task.id]
    assert mine, "recall do overseer deve cair na trilha"
    assert hr.id in mine[-1].payload["hits"]
    assert fin.id in mine[-1].payload["hits"]


def test_template_orchestrator_valido():
    path = Path("src/egr/templates/workspace/agents/orchestrator-agent.yaml")
    specs = load_agent_file(path)

    assert len(specs) == 1
    spec = specs[0]
    assert spec.id == "orchestrator-agent"
    assert "task.delegate" in spec.permissions.tools
    assert spec.permissions.namespaces == ["*"]
    assert spec.memory == ["*"]
