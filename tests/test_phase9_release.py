"""Fase 9 — Production Governance: dev → staging → produção com versão e volta."""

from __future__ import annotations

import pytest

from egr.core.config import EGRConfig, Settings
from egr.core.errors import AuthenticationError, AuthorizationError, ConfigError
from egr.domain.enums import EvaluationTarget, EventType, ReleaseStatus
from egr.domain.evaluation import EvaluationCase, EvaluationSuite, Thresholds
from egr.evaluation.suites import smoke_suite
from egr.runtime.runtime import Runtime

PRECO_TOOL = '''
"""Ferramenta: total de um item com desconto."""

from __future__ import annotations

from egr.domain.enums import RiskLevel
from egr.domain.tool import ToolRequest, ToolResult, ToolSpec
from egr.tools.protocol import Tool


class PrecoTool(Tool):
    spec = ToolSpec(
        name="venda.preco",
        description="calcula o total com desconto",
        parameters={
            "quantidade": {"type": "integer", "required": True},
            "unitario": {"type": "number", "required": True},
            "desconto": {"type": "number", "required": False},
        },
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx) -> ToolResult:
        quantidade = float(request.args["quantidade"])
        unitario = float(request.args["unitario"])
        desconto = float(request.args.get("desconto") or 0)
        return ToolResult.success({"total": round(quantidade * unitario * (1 - desconto / 100), 2)})
'''

BROKEN_TOOL = PRECO_TOOL.replace("(1 - desconto / 100)", "1")

AGENT = """
id: conciliador
name: Agente conciliador
version: 0.3.0
objective: conciliar lancamentos do dia
model:
  capability: reasoning
  temperature: 0.1
memory:
  - default
permissions:
  tools: ["filesystem.*"]
  namespaces: ["default"]
  max_risk: low
environment: development
"""

WILDCARD_AGENT = AGENT.replace(
    "id: conciliador",
    "id: god",
).replace('tools: ["filesystem.*"]', 'tools: ["*"]')


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _apply(kind: str, name: str, source: str, runtime) -> None:
    proposal = runtime.workbench.propose(kind, name, source)
    if kind == "tool":
        runtime.workbench.trial(proposal.id, {"quantidade": 1, "unitario": 1})
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id)


def _apply_tool(runtime, source: str = PRECO_TOOL, name: str = "venda.preco") -> None:
    _apply("tool", name, source, runtime)


def _apply_agent(runtime, source: str = AGENT, name: str = "conciliador") -> None:
    _apply("agent", name, source, runtime)


def _prepare(workspace, apply) -> Runtime:
    """Aplica o artefato num runtime próprio (antes de trocar a config de segurança)."""

    instance = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    apply(instance)
    instance.close()
    return instance


def _suite(**overrides) -> EvaluationSuite:
    """Suíte explícita: o desconto é o caso que separa o certo do quebrado."""

    payload = {
        "id": "venda.preco",
        "name": "Suíte do cálculo de preço",
        "target_kind": EvaluationTarget.TOOL,
        "target": "venda.preco",
        "cases": [
            EvaluationCase(
                id="sem-desconto",
                args={"quantidade": 3, "unitario": 10},
                expect_ok=True,
                expect=["output['total'] == 30.0"],
            ),
            EvaluationCase(
                id="com-desconto",
                args={"quantidade": 2, "unitario": 50, "desconto": 10},
                expect_ok=True,
                expect=["output['total'] == 90.0"],
            ),
        ],
        "thresholds": Thresholds(min_pass_rate=1.0, max_regressions=0),
    }
    payload.update(overrides)
    return EvaluationSuite(**payload)


def _evaluated(runtime, kind: str, name: str):
    """Roda o smoke do artefato e devolve a execução (evidência do release)."""

    suite = smoke_suite(runtime, kind, name)
    runtime.evaluation_suites[suite.id] = suite
    run = runtime.evaluator.run(suite)
    assert run.status == "passed", run.reasons
    return run


def _secured_runtime(workspace, **overrides):
    config = EGRConfig()
    config.security.identity_required = True
    for key, value in overrides.items():
        setattr(config.security, key, value)
    instance = Runtime(Settings(workspace=workspace, config=config), enable_logging=False)
    return instance


# ----------------------------------------------------------------------
# versionamento de artefato
# ----------------------------------------------------------------------
def test_snapshot_versions_the_content(runtime):
    _apply_tool(runtime)
    store = runtime.release_manager.versions

    first = store.snapshot("tool", "venda.preco", actor="vitor")

    assert first.revision == 1
    assert first.fingerprint
    assert "desconto" in first.content
    assert runtime.audit.list(type=EventType.ARTIFACT_VERSIONED)


def test_snapshot_is_a_noop_when_content_did_not_change(runtime):
    _apply_tool(runtime)
    store = runtime.release_manager.versions
    store.snapshot("tool", "venda.preco")

    again = store.snapshot("tool", "venda.preco")

    assert again.revision == 1
    assert len(store.versions("tool", "venda.preco")) == 1


def test_snapshot_creates_a_new_revision_when_content_changes(runtime):
    _apply_tool(runtime)
    store = runtime.release_manager.versions
    first = store.snapshot("tool", "venda.preco")

    path = store.path_for("tool", "venda.preco")
    path.write_text(BROKEN_TOOL, encoding="utf-8")
    second = store.snapshot("tool", "venda.preco")

    assert second.revision == 2
    assert second.fingerprint != first.fingerprint
    assert [item.revision for item in store.versions("tool", "venda.preco")] == [2, 1]


def test_snapshot_refuses_a_missing_artifact(runtime):
    store = runtime.release_manager.versions

    with pytest.raises(ConfigError, match="não encontrado"):
        store.snapshot("tool", "nao.existe")


def test_restore_rewrites_the_content_and_reloads(runtime):
    _apply_tool(runtime)
    store = runtime.release_manager.versions
    first = store.snapshot("tool", "venda.preco")
    store.path_for("tool", "venda.preco").write_text(BROKEN_TOOL, encoding="utf-8")
    store.snapshot("tool", "venda.preco")

    restored = store.restore("tool", "venda.preco", 1, actor="vitor")

    assert restored.revision == 1
    assert store.content_of("tool", "venda.preco") == first.content
    # recarregado: o comportamento volta junto com o conteúdo
    ctx = runtime.adhoc_tool_context("development", task_id="tsk-versao")
    result = runtime.tools.execute("venda.preco", {"quantidade": 2, "unitario": 10, "desconto": 10}, ctx)
    assert result.output["total"] == 18.0


def test_restore_refuses_an_unknown_revision(runtime):
    _apply_tool(runtime)
    store = runtime.release_manager.versions

    with pytest.raises(ConfigError, match="não existe"):
        store.restore("tool", "venda.preco", 99)


def test_set_environment_rewrites_the_declaration(runtime):
    _apply_agent(runtime)
    store = runtime.release_manager.versions

    store.set_environment("agent", "conciliador", "staging", actor="vitor")

    assert "environment: staging" in store.content_of("agent", "conciliador")
    assert runtime.agents["conciliador"].environment == "staging"


# ----------------------------------------------------------------------
# gates
# ----------------------------------------------------------------------
def test_create_release_snapshots_every_item(runtime):
    _apply_tool(runtime)

    release = runtime.release_manager.create([("tool", "venda.preco")], target="staging")

    assert release.status == ReleaseStatus.DRAFT
    assert release.items[0].version == "r1"  # ferramenta não declara versão: vale a revisão
    assert release.items[0].revision == 1
    assert release.items[0].fingerprint
    assert str(release.items[0].from_environment) == "development"
    assert str(release.items[0].to_environment) == "staging"


def test_release_without_items_is_refused(runtime):
    with pytest.raises(ConfigError, match="sem itens"):
        runtime.release_manager.create([], target="staging")


def test_release_to_an_unknown_environment_is_refused(runtime):
    _apply_tool(runtime)

    with pytest.raises(ConfigError, match="ambiente de destino inválido"):
        runtime.release_manager.create([("tool", "venda.preco")], target="homologacao")


def test_gate_requires_an_approved_evaluation(runtime):
    _apply_tool(runtime)  # sem avaliação registrada

    release = runtime.release_manager.create([("tool", "venda.preco")], target="staging")

    assert not release.clear
    assert any("nenhuma avaliação registrada" in check.detail for check in release.errors)
    assert release.evidence == []


def test_gate_fails_for_a_missing_artifact(runtime):
    release = runtime.release_manager.create([("tool", "nao.existe")], target="staging")

    assert any("não existe no workspace" in check.detail for check in release.errors)


def test_gate_does_not_accept_a_failed_evaluation(runtime):
    _apply_tool(runtime, BROKEN_TOOL)
    suite = _suite()
    runtime.evaluation_suites[suite.id] = suite
    run = runtime.evaluator.run(suite)
    assert run.status == "failed"  # o desconto sumiu: a avaliação mediu e reprovou

    release = runtime.release_manager.create([("tool", "venda.preco")], target="staging")

    assert not release.clear
    assert any("terminou em 'failed'" in check.detail for check in release.errors)


def test_gate_blocks_skipping_a_rung(runtime):
    _apply_tool(runtime)

    release = runtime.release_manager.create([("tool", "venda.preco")], target="production")

    assert any("pulou 1 degrau" in check.detail for check in release.errors)
    assert any("produção exige um release aplicado em staging" in check.detail for check in release.errors)


def test_gate_blocks_production_before_staging(runtime):
    _apply_agent(runtime)
    manager = runtime.release_manager
    _evaluated(runtime, "agent", "conciliador")
    staging = _promote(manager, [("agent", "conciliador")], "staging")
    assert str(staging.status) == "deployed"

    # agora a escada permite (o artefato já está em staging)
    production = manager.create([("agent", "conciliador")], target="production")

    assert not any("escada" in check.detail for check in production.errors)
    assert not any("produção exige" in check.detail for check in production.errors)


def test_gate_rejects_a_critical_security_finding(runtime):
    _apply_agent(runtime, WILDCARD_AGENT, "god")

    release = runtime.release_manager.create([("agent", "god")], target="staging")

    assert any(check.name == "segurança" for check in release.errors)


def test_evidence_order_does_not_depend_on_insertion_order(runtime):
    """Duas execuções no mesmo segundo: a mais recente tem que ganhar."""

    _apply_tool(runtime)
    runtime.evaluation_suites["venda.preco"] = _suite()
    first = runtime.evaluator.run(_suite())
    second = runtime.evaluator.run(_suite())

    # empate artificial no banco — o pior caso da ordenação por created_at
    stamp = first.created_at.isoformat(timespec="seconds")
    runtime.db.execute(
        "UPDATE evaluation_runs SET created_at = ? WHERE id IN (?, ?)",
        (stamp, first.id, second.id),
    )
    runtime.db.commit()

    ids = [run.id for run in runtime.evaluations.list(limit=10)]
    assert ids.index(second.id) < ids.index(first.id)


def test_check_refreshes_the_gates(runtime):
    _apply_tool(runtime)
    release = runtime.release_manager.create([("tool", "venda.preco")], target="staging")
    assert not release.clear

    _evaluated(runtime, "tool", "venda.preco")
    checked = runtime.release_manager.check(release.id)

    assert checked.clear
    assert len(checked.evidence) == 1


# ----------------------------------------------------------------------
# ciclo de promoção
# ----------------------------------------------------------------------
def test_submit_fails_while_the_gates_reject(runtime):
    _apply_tool(runtime)
    release = runtime.release_manager.create([("tool", "venda.preco")], target="staging")

    with pytest.raises(ConfigError, match="gates reprovaram"):
        runtime.release_manager.submit(release.id)


def test_submit_moves_the_release_to_review(runtime):
    _apply_tool(runtime)
    _evaluated(runtime, "tool", "venda.preco")
    release = runtime.release_manager.create([("tool", "venda.preco")], target="staging")

    submitted = runtime.release_manager.submit(release.id)

    assert submitted.status == ReleaseStatus.SUBMITTED


def test_deploy_requires_approval(runtime):
    _apply_tool(runtime)
    _evaluated(runtime, "tool", "venda.preco")
    release = runtime.release_manager.submit(
        runtime.release_manager.create([("tool", "venda.preco")], target="staging").id
    )

    with pytest.raises(ConfigError, match="só aprovados são aplicados"):
        runtime.release_manager.deploy(release.id)


def test_approve_requires_a_submitted_release(runtime):
    _apply_tool(runtime)
    release = runtime.release_manager.create([("tool", "venda.preco")], target="staging")

    with pytest.raises(ConfigError, match="nada a aprovar"):
        runtime.release_manager.approve(release.id)


def test_full_cycle_promotes_to_staging(runtime):
    _apply_agent(runtime)
    _evaluated(runtime, "agent", "conciliador")

    release = _promote(runtime.release_manager, [("agent", "conciliador")], "staging")

    assert release.status == ReleaseStatus.DEPLOYED
    assert release.deployed_at is not None
    assert "environment: staging" in runtime.release_manager.versions.content_of("agent", "conciliador")
    # promoção aplica o conteúdo do snapshot e versiona o resultado
    assert [item.revision for item in runtime.release_manager.versions.versions("agent", "conciliador")] == [2, 1]
    assert runtime.audit.list(type=EventType.RELEASE_DEPLOYED)


def test_production_requires_the_staging_rung(runtime):
    _apply_agent(runtime)
    _evaluated(runtime, "agent", "conciliador")
    manager = runtime.release_manager
    _promote(manager, [("agent", "conciliador")], "staging")

    # a evidência é da versão do artefato (0.3.0), que não mudou: a mesma
    # avaliação aprovada continua servindo — o degrau liberado é o ambiente
    production = _promote(manager, [("agent", "conciliador")], "production")

    assert production.status == ReleaseStatus.DEPLOYED
    assert "environment: production" in manager.versions.content_of("agent", "conciliador")


def test_evaluating_in_staging_measures_what_the_artifact_may_do(runtime):
    """Promover muda o governo: em staging `python.execute` passa a exigir humano.

    A avaliação mede essa diferença — não é defeito do release, é o ambiente
    cobrando o preço que ele cobra.
    """

    _apply_agent(runtime)
    manager = runtime.release_manager
    _evaluated(runtime, "agent", "conciliador")
    _promote(manager, [("agent", "conciliador")], "staging")

    suite = smoke_suite(runtime, "agent", "conciliador")
    runtime.evaluation_suites[suite.id] = suite
    verdict = runtime.evaluator.run(suite).status

    assert verdict in ("passed", "failed", "regressed")
    assert str(manager.versions.current("agent", "conciliador").environment) == "staging"


def test_reject_records_the_decision(runtime):
    _apply_tool(runtime)
    _evaluated(runtime, "tool", "venda.preco")
    release = runtime.release_manager.submit(
        runtime.release_manager.create([("tool", "venda.preco")], target="staging").id
    )

    rejected = runtime.release_manager.reject(release.id, "human:vitor", note="ainda não")

    assert rejected.status == ReleaseStatus.REJECTED
    assert rejected.decision_note == "ainda não"
    assert runtime.audit.list(type=EventType.RELEASE_REJECTED)


# ----------------------------------------------------------------------
# rollback
# ----------------------------------------------------------------------
def test_rollback_restores_the_state_before_the_release(runtime):
    _apply_agent(runtime)
    _evaluated(runtime, "agent", "conciliador")
    manager = runtime.release_manager
    manager.versions.snapshot("agent", "conciliador")  # r1: conteúdo original
    path = manager.versions.path_for("agent", "conciliador")
    path.write_text(path.read_text(encoding="utf-8") + "# ajuste antes da promocao\n", encoding="utf-8")
    manager.versions.snapshot("agent", "conciliador")  # r2: o que será promovido

    release = _promote(manager, [("agent", "conciliador")], "staging")

    assert release.items[0].revision == 2
    assert "environment: staging" in manager.versions.content_of("agent", "conciliador")

    undone = manager.rollback(release.id, actor="human:vitor", note="regrediu a conciliação")

    assert undone.status == ReleaseStatus.ROLLED_BACK
    assert undone.rollback_of == release.id
    content = manager.versions.content_of("agent", "conciliador")
    assert "environment: development" in content  # voltou ao ambiente de origem
    assert "# ajuste antes da promocao" in content  # conteúdo promovido, sem o ambiente novo
    assert manager.get(release.id).status == ReleaseStatus.ROLLED_BACK
    assert runtime.audit.list(type=EventType.RELEASE_ROLLED_BACK)


def test_rollback_requires_a_deployed_release(runtime):
    _apply_tool(runtime)
    release = runtime.release_manager.create([("tool", "venda.preco")], target="staging")

    with pytest.raises(ConfigError, match="só aplicados podem ser revertidos"):
        runtime.release_manager.rollback(release.id)


def test_rollback_without_the_captured_snapshot_is_refused(runtime):
    _apply_tool(runtime)
    manager = runtime.release_manager
    _evaluated(runtime, "tool", "venda.preco")
    release = _promote(manager, [("tool", "venda.preco")], "staging")
    # alguém apagou o snapshot: rollback sem versão conhecida é arqueologia, não volta
    runtime.db.execute("DELETE FROM artifact_versions WHERE id = ?", ("tool:venda.preco@1",))
    runtime.db.commit()

    with pytest.raises(ConfigError, match="rollback impossível"):
        manager.rollback(release.id)


# ----------------------------------------------------------------------
# quem pode aprovar
# ----------------------------------------------------------------------
def test_identity_is_required_to_approve(workspace):
    _prepare(workspace, lambda rt: _apply_tool(rt))
    runtime = _secured_runtime(workspace)
    _evaluated(runtime, "tool", "venda.preco")
    release = runtime.release_manager.submit(
        runtime.release_manager.create([("tool", "venda.preco")], target="staging").id
    )

    # sem principal autenticado a promoção não acontece (401 na API, não 403)
    with pytest.raises(AuthenticationError, match="identidade não verificada"):
        runtime.release_manager.approve(release.id, "human:ninguem")


def test_approving_without_the_permission_is_denied(workspace):
    _prepare(workspace, lambda rt: _apply_tool(rt))
    runtime = _secured_runtime(workspace)
    runtime.identity.create_principal("viewer.vitor", roles=["viewer"])
    _evaluated(runtime, "tool", "venda.preco")
    release = runtime.release_manager.submit(
        runtime.release_manager.create([("tool", "venda.preco")], target="staging").id
    )

    with pytest.raises(AuthorizationError, match="não tem a permissão"):
        runtime.release_manager.approve(release.id, "viewer.vitor")


def test_production_demands_a_higher_role_than_staging(workspace):
    _prepare(workspace, lambda rt: _apply_agent(rt))
    runtime = _secured_runtime(workspace)
    runtime.identity.create_principal("op.vitor", roles=["operator"])
    manager = runtime.release_manager
    _evaluated(runtime, "agent", "conciliador")

    staging = manager.submit(manager.create([("agent", "conciliador")], target="staging").id)
    approved = manager.approve(staging.id, "op.vitor")
    assert approved.decided_by == "op.vitor"
    manager.deploy(staging.id, actor="op.vitor")

    production = manager.submit(manager.create([("agent", "conciliador")], target="production").id)
    with pytest.raises(AuthorizationError, match="exige 'approver'"):
        manager.approve(production.id, "op.vitor")

    runtime.close()


def test_an_agent_cannot_approve_a_promotion(workspace):
    _prepare(workspace, lambda rt: _apply_tool(rt))
    runtime = _secured_runtime(workspace)
    runtime.identity.create_principal("agente.builder", kind="agent", roles=["operator"])
    _evaluated(runtime, "tool", "venda.preco")
    release = runtime.release_manager.submit(
        runtime.release_manager.create([("tool", "venda.preco")], target="staging").id
    )

    with pytest.raises(AuthorizationError, match="não pode aprovar promoção"):
        runtime.release_manager.approve(release.id, "agente.builder")


def test_a_verified_token_approves(workspace):
    _prepare(workspace, lambda rt: _apply_tool(rt))
    runtime = _secured_runtime(workspace)
    runtime.identity.create_principal("vitor", roles=["approver"])
    raw, _ = runtime.identity.issue_token("vitor")
    _evaluated(runtime, "tool", "venda.preco")
    release = runtime.release_manager.submit(
        runtime.release_manager.create([("tool", "venda.preco")], target="staging").id
    )

    approved = runtime.release_manager.approve(release.id, "vitor", token=raw, note="ok")

    assert approved.decided_by == "vitor"
    assert approved.status == ReleaseStatus.APPROVED


def test_a_revoked_token_does_not_approve(workspace):
    _prepare(workspace, lambda rt: _apply_tool(rt))
    runtime = _secured_runtime(workspace)
    runtime.identity.create_principal("vitor", roles=["approver"])
    raw, record = runtime.identity.issue_token("vitor")
    runtime.identity.revoke_token(record.id)
    _evaluated(runtime, "tool", "venda.preco")
    release = runtime.release_manager.submit(
        runtime.release_manager.create([("tool", "venda.preco")], target="staging").id
    )

    with pytest.raises(AuthenticationError, match="identidade não verificada"):
        runtime.release_manager.approve(release.id, "vitor", token=raw)


# ----------------------------------------------------------------------
# superfície
# ----------------------------------------------------------------------
def test_governance_status_reports_the_ladder(runtime):
    _apply_agent(runtime)
    _evaluated(runtime, "agent", "conciliador")
    _promote(runtime.release_manager, [("agent", "conciliador")], "staging")

    data = runtime.governance_status()

    assert data["ladder"] == ["development", "staging", "production"]
    assert data["releases"]["total"] == 1
    assert data["releases"]["by_status"]["deployed"] == 1
    assert data["deployed"]["staging"] == ["agent:conciliador@0.3.0"]
    assert "agent:conciliador" in data["versions"]["artifacts"]


def test_runtime_status_carries_governance(runtime):
    assert "governance" in runtime.status()


def test_health_reports_releases(runtime):
    _apply_tool(runtime)
    _evaluated(runtime, "tool", "venda.preco")
    _promote(runtime.release_manager, [("tool", "venda.preco")], "staging")

    checks = runtime.health()["checks"]
    governance = next(check for check in checks if check["check"] == "governance")

    assert governance["ok"] is True
    assert "1 release(s)" in governance["detail"]


def test_release_list_and_show(runtime):
    _apply_tool(runtime)
    _evaluated(runtime, "tool", "venda.preco")
    release = _promote(runtime.release_manager, [("tool", "venda.preco")], "staging")

    assert [item.id for item in runtime.release_manager.list()] == [release.id]
    assert [item.id for item in runtime.release_manager.list(status="draft")] == []
    assert runtime.release_manager.get(release.id).summary()["gates"]
    with pytest.raises(ConfigError, match="não encontrado"):
        runtime.release_manager.get("release_nao_existe")


def test_deploy_refuses_when_the_gates_turned_red(runtime):
    _apply_tool(runtime)
    manager = runtime.release_manager
    _evaluated(runtime, "tool", "venda.preco")
    release = manager.submit(manager.create([("tool", "venda.preco")], target="staging").id)
    manager.approve(release.id, "human:vitor")

    # depois da aprovação alguém mexeu no artefato e a avaliação passou a reprovar
    manager.versions.path_for("tool", "venda.preco").write_text(BROKEN_TOOL, encoding="utf-8")
    runtime.workbench.reload_tools()
    runtime.evaluation_suites["venda.preco"] = _suite()
    assert runtime.evaluator.run(_suite()).status == "failed"

    with pytest.raises(ConfigError, match="reavalie antes de aplicar"):
        manager.deploy(release.id)


def test_rollback_is_audited_end_to_end(runtime):
    _apply_agent(runtime)
    _evaluated(runtime, "agent", "conciliador")
    manager = runtime.release_manager
    release = _promote(manager, [("agent", "conciliador")], "staging")
    manager.rollback(release.id)

    kinds = {event.type for event in runtime.audit.list(limit=200)}

    assert {
        EventType.RELEASE_CREATED,
        EventType.RELEASE_SUBMITTED,
        EventType.RELEASE_APPROVED,
        EventType.RELEASE_DEPLOYED,
        EventType.RELEASE_ROLLED_BACK,
        EventType.ARTIFACT_VERSIONED,
    } <= kinds


# ----------------------------------------------------------------------
# helpers de teste
# ----------------------------------------------------------------------
def _promote(manager, items, target, *, by: str = "human:vitor"):
    """create → submit → approve → deploy (o caminho completo).

    Lacuna 9b: produção exige quórum — então o segundo voto entra aqui mesmo,
    e o teste continua exercitando o caminho feliz do jeito que a política manda.
    """

    release = manager.create(items, target=target, created_by=by)
    manager.submit(release.id)
    manager.approve(release.id, by)
    required = manager.runtime.settings.config.release.signature_environments
    if target == "production":
        manager.runtime.keystore.create()
        manager.approve(release.id, "human:revisora", note="primeiro voto do quórum")
        manager.approve(release.id, "human:auditora", note="segundo voto do quórum")
        if target in required:
            manager.sign(release.id, actor=by)
    return manager.deploy(release.id, actor=by)


def test_authentication_error_is_exported_for_the_api():
    # a API traduz identidade inválida em 401; o contrato precisa existir
    assert issubclass(AuthenticationError, Exception)
