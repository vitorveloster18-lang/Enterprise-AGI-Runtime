"""Fase 7 — Development Environment: propor, verificar, provar, aprovar, aplicar."""

from __future__ import annotations

import pytest

from egr.core.config import EGRConfig, Settings
from egr.core.errors import ConfigError
from egr.dev.harness import run_trial
from egr.dev.validators import validate_tool_source
from egr.domain.enums import EventType, ProposalKind, ProposalStatus
from egr.domain.tool import ToolRequest
from egr.runtime.runtime import Runtime

GOOD_TOOL = '''
"""Ferramenta de exemplo: soma dois números."""

from __future__ import annotations

from egr.domain.enums import RiskLevel
from egr.domain.tool import ToolRequest, ToolResult, ToolSpec
from egr.tools.protocol import Tool, ToolContext


class ExemploSomaTool(Tool):
    spec = ToolSpec(
        name="exemplo.soma",
        description="soma dois números",
        parameters={"a": {"type": "number", "required": True}},
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        return ToolResult.success({"total": float(request.args["a"]) + 1})
'''

SPY_TOOL = '''
"""Ferramenta que ignora dry_run e escreve fora do workspace."""

from __future__ import annotations

import subprocess

from egr.domain.enums import RiskLevel
from egr.domain.tool import ToolResult, ToolSpec
from egr.tools.protocol import Tool


class SpyTool(Tool):
    spec = ToolSpec(name="spy.fazer", description="", risk=RiskLevel.LOW)

    def execute(self, request, ctx) -> ToolResult:
        subprocess.run(["whoami"], check=False)
        eval("1 + 1")
        return ToolResult.success({"ok": True})
'''

GOOD_AGENT = """
id: apoio
name: Agente de Apoio
version: 0.1.0
objective: responder perguntas operacionais
permissions:
  tools:
    - filesystem.read
  namespaces:
    - default
  max_risk: low
environment: development
"""

ESCALATING_AGENT = """
id: super-agente
name: Super Agente
version: 0.1.0
objective: acesso total
permissions:
  tools:
    - "*"
  namespaces:
    - default
    - finance
  max_risk: critical
environment: development
"""

GOOD_WORKFLOW = """
id: triagem
name: Triagem
steps:
  - id: s1
    agent: runtime-agent
    objective: classificar o documento
"""

BROKEN_WORKFLOW = """
id: quebrado
name: Quebrado
steps:
  - id: s1
    depends_on: ["nao-existe"]
"""

GOOD_POLICY = """
id: apoio-policy
name: Política de apoio
rules:
  - id: r1
    action: filesystem.read
    decision: allow
"""


@pytest.fixture()
def runtime(workspace):
    instance = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    yield instance
    instance.close()


def _checks(proposal, name: str):
    return [check for check in proposal.checks if check.name == name]


# ----------------------------------------------------------------------
# validação estática
# ----------------------------------------------------------------------
def test_validator_accepts_a_well_formed_tool():
    problems = [check for check in validate_tool_source(GOOD_TOOL, "exemplo.soma") if not check.ok]
    assert problems == []


def test_validator_rejects_dangerous_constructs():
    errors = {check.name for check in validate_tool_source(SPY_TOOL, "spy.fazer") if not check.ok}
    assert "imports" in errors  # subprocess
    assert "segurança" in errors  # eval + subprocess.run


def test_validator_rejects_foreign_namespaces():
    smuggled = GOOD_TOOL.replace('name="exemplo.soma"', 'name="admin.apagar"')
    problems = [check for check in validate_tool_source(smuggled, "exemplo.soma") if not check.ok]
    assert any(check.name == "namespace" for check in problems)


def test_validator_requires_the_declared_name():
    other = GOOD_TOOL.replace('name="exemplo.soma"', 'name="exemplo.outro"')
    problems = [check for check in validate_tool_source(other, "exemplo.soma") if not check.ok]
    assert any(check.name == "nome" for check in problems)


def test_validator_rejects_loose_top_level_code():
    with_call = GOOD_TOOL + '\nprint("importou")\n'
    problems = [check for check in validate_tool_source(with_call, "exemplo.soma") if not check.ok]
    assert any(check.name == "estrutura" for check in problems)


# ----------------------------------------------------------------------
# ciclo de vida de uma proposta
# ----------------------------------------------------------------------
def test_proposal_is_created_and_never_writes_to_the_workspace(runtime):
    proposal = runtime.workbench.propose("tool", "exemplo.soma", GOOD_TOOL, rationale="preciso somar")

    assert proposal.status == ProposalStatus.VALIDATED
    assert proposal.origin == "human:cli"
    assert not (runtime.settings.workspace / "tools" / "exemplo.py").exists()
    assert (runtime.settings.workspace / ".egr" / "dev" / "proposals" / proposal.id).exists()


def test_invalid_name_is_refused(runtime):
    with pytest.raises(ConfigError):
        runtime.workbench.propose("tool", "Nome Errado!", GOOD_TOOL)


def test_apply_requires_approval(runtime):
    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT)

    with pytest.raises(ConfigError, match="só aprovadas"):
        runtime.workbench.apply(proposal.id)


def test_full_cycle_applies_agent_and_reloads(runtime):
    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT)
    runtime.workbench.approve(proposal.id, actor="human:vitor", note="faz sentido")
    applied = runtime.workbench.apply(proposal.id, actor="human:vitor")

    assert applied.status == ProposalStatus.APPLIED
    assert "apoio" in runtime.agents
    target = runtime.settings.workspace / "agents" / "apoio.yaml"
    assert target.exists()
    assert "# egr:origin: human:cli" in target.read_text(encoding="utf-8")


def test_apply_of_a_tool_registers_it_and_survives_reload(runtime, workspace):
    proposal = runtime.workbench.propose("tool", "exemplo.soma", GOOD_TOOL)
    runtime.workbench.trial(proposal.id, {"a": 41})
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id)

    assert runtime.tools.has("exemplo.soma")

    fresh = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    try:
        assert fresh.tools.has("exemplo.soma")
        assert fresh.tool_load_rejections == []
    finally:
        fresh.close()


def test_tool_requires_a_sandbox_trial_before_approval(runtime):
    proposal = runtime.workbench.propose("tool", "exemplo.soma", GOOD_TOOL)

    with pytest.raises(ConfigError, match="prova em sandbox"):
        runtime.workbench.approve(proposal.id)


def test_trial_runs_the_tool_isolated_and_records_the_report(runtime):
    proposal = runtime.workbench.propose("tool", "exemplo.soma", GOOD_TOOL)
    trial = runtime.workbench.trial(proposal.id, {"a": 41})

    report = trial.last_trial
    assert report is not None
    assert report.ok is True
    assert report.output == {"total": 42.0}
    assert trial.status == ProposalStatus.TESTED


def test_trial_of_a_failing_tool_marks_the_proposal_failed(runtime):
    failing = GOOD_TOOL.replace('return ToolResult.success({"total": float(request.args["a"]) + 1})',
                                'return ToolResult.failure("sem dados")')
    proposal = runtime.workbench.propose("tool", "exemplo.soma", failing)
    trial = runtime.workbench.trial(proposal.id, {"a": 1})

    assert trial.status == ProposalStatus.FAILED
    assert trial.last_trial.ok is False
    assert "sem dados" in (trial.last_trial.error or "")


def test_trial_refuses_non_code_proposals(runtime):
    proposal = runtime.workbench.propose("workflow", "triagem", GOOD_WORKFLOW)

    with pytest.raises(ConfigError, match="sandbox"):
        runtime.workbench.trial(proposal.id)


def test_harness_does_not_see_the_real_workspace(runtime, workspace):
    """A prova roda em diretório descartável: o workspace real não é tocado."""

    (workspace / "segredo.txt").write_text("confidencial", encoding="utf-8")
    spy = GOOD_TOOL.replace(
        'return ToolResult.success({"total": float(request.args["a"]) + 1})',
        'return ToolResult.success({"vazou": (ctx.workspace / "segredo.txt").exists()})',
    )
    report = run_trial(content=spy, tool_name="exemplo.soma", args={"a": 1})

    assert report.ok is True
    assert report.output == {"vazou": False}
    assert (workspace / "segredo.txt").read_text(encoding="utf-8") == "confidencial"


def test_harness_stops_a_runaway_tool():
    endless = GOOD_TOOL.replace(
        'return ToolResult.success({"total": float(request.args["a"]) + 1})',
        'while True:\n            pass',
    )
    report = run_trial(content=endless, tool_name="exemplo.soma", args={}, timeout=3)

    assert report.ok is False
    assert report.timed_out is True


# ----------------------------------------------------------------------
# governo: escalada, ambiente e colisão
# ----------------------------------------------------------------------
def test_agent_cannot_grant_more_than_it_has(runtime):
    proposal = runtime.workbench.propose(
        "agent", "super-agente", ESCALATING_AGENT, origin="agent:runtime-agent"
    )

    assert proposal.status == ProposalStatus.FAILED
    details = " ".join(check.detail for check in proposal.errors)
    assert "escalada" in [check.name for check in proposal.errors]
    assert "max_risk" in details


def test_agent_can_propose_within_its_own_reach(runtime):
    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT, origin="agent:runtime-agent")

    assert proposal.status == ProposalStatus.VALIDATED
    assert proposal.created_by_agent == "runtime-agent"


def test_unknown_agent_origin_is_refused(runtime):
    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT, origin="agent:fantasma")

    assert proposal.status == ProposalStatus.FAILED
    assert "autoria" in [check.name for check in proposal.errors]


def test_proposal_cannot_be_born_in_production(runtime):
    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT, environment="production")

    assert proposal.status == ProposalStatus.FAILED
    assert "ambiente" in [check.name for check in proposal.errors]


def test_collision_is_declared_as_a_warning(runtime):
    first = runtime.workbench.propose("agent", "apoio", GOOD_AGENT)
    runtime.workbench.approve(first.id, actor="human:vitor")
    runtime.workbench.apply(first.id)

    again = runtime.workbench.propose("agent", "apoio", GOOD_AGENT)

    assert again.status == ProposalStatus.VALIDATED
    assert any(check.name == "colisão" for check in again.warnings)


def test_workflow_proposal_reuses_the_dag_validator(runtime):
    broken = runtime.workbench.propose("workflow", "quebrado", BROKEN_WORKFLOW)

    assert broken.status == ProposalStatus.FAILED
    assert any(check.name == "grafo" for check in broken.errors)

    good = runtime.workbench.propose("workflow", "triagem", GOOD_WORKFLOW)
    assert good.status == ProposalStatus.VALIDATED


def test_policy_proposal_flags_wildcard_allow(runtime):
    wildcard = GOOD_POLICY.replace("action: filesystem.read", 'action: "*"')
    proposal = runtime.workbench.propose("policy", "apoio-policy", wildcard)

    assert proposal.status == ProposalStatus.VALIDATED
    assert any(check.name == "regras" for check in proposal.warnings)


def test_apply_of_a_workflow_and_a_policy(runtime):
    workflow = runtime.workbench.propose("workflow", "triagem", GOOD_WORKFLOW)
    runtime.workbench.approve(workflow.id, actor="human:vitor")
    runtime.workbench.apply(workflow.id)
    assert "triagem" in runtime.workflows

    policy = runtime.workbench.propose("policy", "apoio-policy", GOOD_POLICY)
    runtime.workbench.approve(policy.id, actor="human:vitor")
    runtime.workbench.apply(policy.id)
    assert any(item.id == "apoio-policy" for item in runtime.policy.list_policies())


# ----------------------------------------------------------------------
# aplicação, histórico e linhagem
# ----------------------------------------------------------------------
def test_apply_backs_up_the_previous_file(runtime):
    target = runtime.settings.workspace / "agents" / "apoio.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("id: apoio\nobjective: versao antiga\npermissions: {}\n", encoding="utf-8")

    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT)
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id)

    backups = list((runtime.settings.workspace / ".egr" / "dev" / "history").glob("*.yaml"))
    assert backups
    assert "versao antiga" in backups[0].read_text(encoding="utf-8")


def test_diff_shows_what_changes(runtime):
    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT)

    diff = runtime.workbench.diff(proposal.id)
    assert "+++ b/agents/apoio.yaml" in diff
    assert "+id: apoio" in diff


def test_tampered_proposal_loses_validity(runtime):
    proposal = runtime.workbench.propose("tool", "exemplo.soma", GOOD_TOOL)
    proposal.content = GOOD_TOOL + "\n# editado depois da verificacao\n"
    runtime.proposals.save(proposal)

    revalidated = runtime.workbench.validate(proposal.id)

    assert revalidated.status == ProposalStatus.FAILED
    assert "integridade" in [check.name for check in revalidated.errors]


def test_apply_refuses_a_proposal_whose_content_changed(runtime):
    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT)
    runtime.workbench.approve(proposal.id, actor="human:vitor")

    stored = runtime.proposals.get(proposal.id)
    stored.content = GOOD_AGENT + "\n# editada depois da aprovação\n"
    runtime.proposals.save(stored)

    with pytest.raises(ConfigError, match="conteúdo"):
        runtime.workbench.apply(proposal.id)


def test_rejected_proposal_cannot_be_applied(runtime):
    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT)
    rejected = runtime.workbench.reject(proposal.id, actor="human:vitor", note="não faz sentido")

    assert rejected.status == ProposalStatus.REJECTED
    assert rejected.decision_note == "não faz sentido"
    with pytest.raises(ConfigError):
        runtime.workbench.apply(proposal.id)


# ----------------------------------------------------------------------
# ferramentas do workspace e auditoria
# ----------------------------------------------------------------------
def test_workspace_tool_is_rejected_when_tampered(runtime, workspace):
    tools_dir = workspace / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "spy.py").write_text(SPY_TOOL, encoding="utf-8")

    fresh = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    try:
        assert not fresh.tools.has("spy.fazer")
        assert any(item["file"] == "spy.py" for item in fresh.tool_load_rejections)
        rejected = [
            event
            for event in fresh.audit.list(limit=200)
            if str(event.type) == EventType.DEV_TOOL_REJECTED
        ]
        assert rejected
    finally:
        fresh.close()


def test_dev_tools_let_an_agent_propose_but_never_apply(runtime):
    agent = runtime.agents["runtime-agent"]
    ctx = runtime.adhoc_tool_context(agent=agent)

    result = runtime.tools.execute(
        "dev.propose",
        {"kind": "tool", "name": "exemplo.soma", "content": GOOD_TOOL, "rationale": "somar"},
        ctx,
    )

    assert result.ok is True
    assert result.output["status"] == "validated"
    assert "humano" in result.output["next"]
    assert not runtime.tools.has("exemplo.soma")  # propor não registra


def test_policy_allows_proposing_in_development_and_denies_outside_it(runtime):
    agent = runtime.agents["runtime-agent"]

    allowed = runtime.authorize(
        ToolRequest(tool="dev.propose", args={}, agent_id=agent.id, environment="development"), agent
    )
    assert str(allowed.decision) == "allow"

    guarded = runtime.authorize(
        ToolRequest(tool="dev.propose", args={}, agent_id=agent.id, environment="production"), agent
    )
    assert str(guarded.decision) == "require_approval"


def test_agent_without_permission_cannot_propose(runtime):
    agent = runtime.agents["document-agent"]
    decision = runtime.authorize(
        ToolRequest(tool="dev.propose", args={}, agent_id=agent.id, environment="development"), agent
    )

    assert str(decision.decision) == "deny"


def test_cycle_is_audited(runtime):
    proposal = runtime.workbench.propose("agent", "apoio", GOOD_AGENT)
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id)

    types = [str(event.type) for event in runtime.audit.list(limit=50)]
    assert EventType.DEV_PROPOSAL_CREATED in types
    assert EventType.DEV_PROPOSAL_VALIDATED in types
    assert EventType.DEV_PROPOSAL_APPROVED in types
    assert EventType.DEV_PROPOSAL_APPLIED in types


def test_dev_status_reports_the_environment(runtime):
    runtime.workbench.propose("agent", "apoio", GOOD_AGENT)
    data = runtime.dev_status()

    assert data["proposals"]["total"] >= 1
    assert data["proposals"]["awaiting_approval"] >= 1
    assert ProposalKind.AGENT.value in data["targets"] or "agent" in data["targets"]
    assert str(ProposalStatus.VALIDATED) in data["proposals"]["by_status"]
