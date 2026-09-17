"""Fase 13 — lacuna 9b: promoção assinada e com quórum.

Uma promoção tem duas fraquezas clássicas: aprovar um nome e aplicar outro
conteúdo, e aprovar sozinho o que devia ter mais de um olhar. A assinatura mata
a primeira, o quórum mata a segunda.

Nada aqui é automático: a assinatura é um ato (alguém assina), o quórum é gente
(dois votos de pessoas diferentes) e o deploy recusa os dois quando faltam.
"""

from __future__ import annotations

import pytest

from egr.core.config import EGRConfig, Settings, dump_config
from egr.core.errors import ConfigError
from egr.domain.enums import EventType
from egr.domain.release import Release
from egr.release.signature import manifest, manifest_hash, sign, verify
from egr.runtime.runtime import Runtime

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


def _apply(runtime: Runtime, kind: str, name: str, content: str) -> None:
    """Propõe, testa, aprova e aplica: o artefato entra pelo caminho governado."""

    proposal = runtime.workbench.propose(kind, name, content)
    if kind == "tool":  # pragma: no cover - o teste usa agente
        runtime.workbench.trial(proposal.id, {"quantidade": 1, "unitario": 1})
    runtime.workbench.approve(proposal.id, actor="human:vitor")
    runtime.workbench.apply(proposal.id)


def _apply_agent(runtime: Runtime) -> None:
    _apply(runtime, "agent", "conciliador", AGENT)


def _evaluated(runtime: Runtime, kind: str, name: str):
    from egr.evaluation.suites import smoke_suite

    suite = smoke_suite(runtime, kind, name)
    runtime.evaluation_suites[suite.id] = suite
    run = runtime.evaluator.run(suite)
    assert run.status == "passed", run.reasons
    return run


def _to_staging(runtime: Runtime, by: str = "human:vitor") -> None:
    """Sobe o degrau obrigatório: ninguém chega em produção sem passar por aqui."""

    _apply_agent(runtime)
    _evaluated(runtime, "agent", "conciliador")
    release = runtime.release_manager.create(
        [("agent", "conciliador")], target="staging", created_by=by
    )
    runtime.release_manager.submit(release.id)
    runtime.release_manager.approve(release.id, by)
    runtime.release_manager.deploy(release.id, actor=by)


def _release(manager, target: str = "staging", by: str = "human:vitor") -> Release:
    if target == "production":
        _to_staging(manager.runtime, by=by)
    else:
        _apply_agent(manager.runtime)
        _evaluated(manager.runtime, "agent", "conciliador")
    return manager.create([("agent", "conciliador")], target=target, created_by=by)


def _event_types(runtime: Runtime, release_id: str) -> set:
    """Tipos de evento daquele release (a auditoria é compartilhada)."""

    return {
        event.type
        for event in runtime.audit.list(limit=400)
        if (event.payload or {}).get("release") == release_id
    }


def _submitted(manager, target: str = "staging", by: str = "human:vitor") -> Release:
    """Release submetido. Em staging o autor aprova sozinho; em produção, não."""

    release = _release(manager, target=target, by=by)
    manager.submit(release.id)
    if target == "staging":
        manager.approve(release.id, by)
    return manager.get(release.id)


def _locked(runtime: Runtime, **overrides) -> Runtime:
    """Runtime com política de promoção ajustada (o default já exige quórum)."""

    config = EGRConfig()
    for key, value in overrides.items():
        setattr(config.release, key, value)
    return Runtime(Settings(workspace=runtime.settings.workspace, config=config), enable_logging=False)


# ----------------------------------------------------------------------
# assinatura
# ----------------------------------------------------------------------
def test_manifest_covers_the_whole_promotion(runtime):
    release = _submitted(runtime.release_manager)

    data = manifest(release)

    assert data["release"] == release.id
    assert data["destino"] == "staging"
    assert [item["artefato"] for item in data["itens"]] == ["agent:conciliador"]
    # evidência entra no manifesto: promover sem avaliação aprovada muda o hash
    assert data["evidência"] == sorted(release.evidence)


def test_manifest_hash_changes_with_the_content(runtime):
    release = _submitted(runtime.release_manager)
    before = manifest_hash(release)

    release.items[0].version = "9.9.9"

    assert manifest_hash(release) != before


def test_sign_stamps_key_and_content(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager)

    signature = sign(runtime, release, actor="human:vitor")

    assert signature.value
    assert signature.key_id == runtime.keystore.require().key_id
    assert signature.algorithm == "hmac-sha256"
    assert signature.signed_by == "human:vitor"
    # a chave nunca aparece: só a impressão dela
    assert runtime.keystore.require().raw.hex() not in signature.model_dump_json()

    assert EventType.RELEASE_SIGNED in _event_types(runtime, release.id)


def test_verify_accepts_the_signed_release(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager)
    sign(runtime, release, actor="human:vitor")

    valid, detail = verify(runtime, release)

    assert valid is True
    assert "válida" in detail


def test_verify_rejects_a_release_that_changed_after_signing(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager)
    sign(runtime, release, actor="human:vitor")

    release.items[0].revision = 99
    valid, detail = verify(runtime, release)

    assert valid is False
    assert "mudou depois de assinado" in detail


def test_verify_rejects_a_forged_signature(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager)
    sign(runtime, release, actor="human:vitor")

    release.signature.value = "0" * 64
    valid, detail = verify(runtime, release)

    assert valid is False
    assert "assinatura não confere" in detail


def test_verify_rejects_an_unsigned_release(runtime):
    release = _submitted(runtime.release_manager)

    valid, detail = verify(runtime, release)

    assert valid is False
    assert "sem assinatura" in detail


def test_verify_after_key_rotation_says_the_key_changed(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager)
    sign(runtime, release, actor="human:vitor")
    runtime.keystore.rotate(force=True)

    valid, detail = verify(runtime, release)

    assert valid is False
    assert "outra chave" in detail


def test_manager_sign_and_verify_round_trip(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager)

    signed = runtime.release_manager.sign(release.id, actor="human:vitor")
    result = runtime.release_manager.verify(release.id)

    assert signed.signature is not None
    assert result["válida"] is True
    assert result["assinatura"]["assinado_por"] == "human:vitor"
    assert result["quórum"]["destino"] == "staging"


def test_signing_a_draft_is_refused(runtime):
    runtime.keystore.create()
    release = _release(runtime.release_manager)

    with pytest.raises(ConfigError, match="nada a assinar"):
        runtime.release_manager.sign(release.id, actor="human:vitor")


# ----------------------------------------------------------------------
# quórum
# ----------------------------------------------------------------------
def test_production_needs_more_than_one_vote(runtime):
    release = _submitted(runtime.release_manager, target="production")

    state = runtime.release_manager.quorum(release)

    assert state["exigido"] == 2
    assert state["obtido"] == 0
    assert state["completo"] is False


def test_staging_keeps_the_single_vote(runtime):
    release = _submitted(runtime.release_manager, target="staging")

    assert runtime.release_manager.quorum(release)["exigido"] == 1


def test_the_first_vote_does_not_approve_production(runtime):
    release = _submitted(runtime.release_manager, target="production")

    pending = runtime.release_manager.approve(release.id, "human:revisora")

    assert str(pending.status) == "submitted"
    assert runtime.release_manager.quorum(pending)["obtido"] == 1

    kinds = _event_types(runtime, release.id)
    assert EventType.RELEASE_APPROVAL_RECORDED in kinds
    assert EventType.RELEASE_APPROVED not in kinds


def test_two_people_close_the_quorum(runtime):
    release = _submitted(runtime.release_manager, target="production")

    runtime.release_manager.approve(release.id, "human:revisora")
    approved = runtime.release_manager.approve(release.id, "human:auditora", note="ok")

    assert str(approved.status) == "approved"
    assert runtime.release_manager.quorum(approved)["completo"] is True
    assert "revisora" in approved.decided_by


def test_nobody_votes_twice(runtime):
    release = _submitted(runtime.release_manager, target="production")
    runtime.release_manager.approve(release.id, "human:revisora")

    with pytest.raises(ConfigError, match="já votou"):
        runtime.release_manager.approve(release.id, "human:revisora")


def test_the_author_does_not_count_for_the_production_quorum(runtime):
    release = _submitted(runtime.release_manager, target="production", by="human:vitor")

    runtime.release_manager.approve(release.id, "human:vitor")
    state = runtime.release_manager.quorum(runtime.release_manager.get(release.id))

    assert state["aprovadores"] == []
    assert state["completo"] is False


def test_a_veto_blocks_the_promotion(runtime):
    release = _submitted(runtime.release_manager, target="production")
    runtime.release_manager.approve(release.id, "human:revisora")

    runtime.release_manager.reject(release.id, "human:auditora", note="evidência fraca")
    state = runtime.release_manager.quorum(runtime.release_manager.get(release.id))

    assert state["vetos"] == ["auditora"]
    assert state["completo"] is False


def test_quorum_is_configurable_per_workspace(runtime):
    instance = _locked(runtime, min_approvals=1, min_approvals_production=1)
    try:
        _to_staging(instance)
        release = instance.release_manager.create(
            [("agent", "conciliador")], target="production", created_by="human:vitor"
        )
        instance.release_manager.submit(release.id)

        approved = instance.release_manager.approve(release.id, "human:vitor")

        assert str(approved.status) == "approved"
    finally:
        instance.close()


# ----------------------------------------------------------------------
# barreiras no deploy
# ----------------------------------------------------------------------
def test_production_without_signature_is_refused(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager, target="production")
    runtime.release_manager.approve(release.id, "human:revisora")
    runtime.release_manager.approve(release.id, "human:auditora")

    with pytest.raises(ConfigError, match="exige assinatura válida"):
        runtime.release_manager.deploy(release.id, actor="human:vitor")


def test_signed_production_deploys(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager, target="production")
    runtime.release_manager.approve(release.id, "human:revisora")
    runtime.release_manager.approve(release.id, "human:auditora")
    runtime.release_manager.sign(release.id, actor="human:revisora")

    deployed = runtime.release_manager.deploy(release.id, actor="human:vitor")

    assert str(deployed.status) == "deployed"
    assert runtime.release_manager.verify(deployed.id)["válida"] is True


def test_staging_does_not_need_a_signature(runtime):
    release = _submitted(runtime.release_manager, target="staging")

    deployed = runtime.release_manager.deploy(release.id, actor="human:vitor")

    assert str(deployed.status) == "deployed"
    assert deployed.signature is None


def test_approving_one_thing_and_deploying_another_is_refused(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager, target="production")
    runtime.release_manager.approve(release.id, "human:revisora")
    runtime.release_manager.approve(release.id, "human:auditora")
    runtime.release_manager.sign(release.id, actor="human:revisora")

    # alguém mexe no release depois da aprovação: o manifesto não é mais o votado
    stored = runtime.release_manager.get(release.id)
    stored.items[0].version = "7.7.7"
    runtime.release_manager._save(stored)

    with pytest.raises(ConfigError, match="mudou depois da aprovação"):
        runtime.release_manager.deploy(release.id, actor="human:vitor")


def test_gates_report_the_missing_signature_as_a_warning(runtime):
    release = _submitted(runtime.release_manager, target="production")

    names = {check.name: check for check in release.checks}

    assert "assinatura" in names
    assert names["assinatura"].ok is False
    assert names["assinatura"].level == "warning"


def test_summary_carries_signature_and_approvers(runtime):
    runtime.keystore.create()
    release = _submitted(runtime.release_manager, target="production")
    runtime.release_manager.approve(release.id, "human:revisora")
    runtime.release_manager.approve(release.id, "human:auditora")
    signed = runtime.release_manager.sign(release.id, actor="human:revisora")

    summary = signed.summary()

    assert summary["assinado"] is True
    assert summary["aprovadores"] == ["revisora", "auditora"]
    assert summary["vetos"] == 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _cli(workspace):
    from typer.testing import CliRunner

    from egr.cli.commands.releases import app as release_app

    return CliRunner(), release_app


def test_cli_sign_verify_and_approvals(workspace):
    from egr.core.config import EGRConfig, Settings
    from egr.runtime.runtime import Runtime

    instance = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    instance.keystore.create()
    _to_staging(instance)
    release = instance.release_manager.create(
        [("agent", "conciliador")], target="production", created_by="human:vitor"
    )
    instance.release_manager.submit(release.id)
    instance.close()

    runner, app = _cli(workspace)

    # sem assinatura: verify sai com 1
    missing = runner.invoke(app, ["verify", release.id, "-w", str(workspace)])
    assert missing.exit_code == 1
    assert "inválida" in missing.output

    signed = runner.invoke(app, ["sign", release.id, "--by", "human:vitor", "-w", str(workspace)])
    assert signed.exit_code == 0, signed.output
    assert "assinado" in signed.output

    ok = runner.invoke(app, ["verify", release.id, "-w", str(workspace)])
    assert ok.exit_code == 0
    assert "válida" in ok.output


def test_cli_approve_shows_how_many_votes_are_missing(workspace):
    from egr.core.config import EGRConfig, Settings
    from egr.runtime.runtime import Runtime

    instance = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    _to_staging(instance)
    release = instance.release_manager.create(
        [("agent", "conciliador")], target="production", created_by="human:vitor"
    )
    instance.release_manager.submit(release.id)
    instance.close()

    runner, app = _cli(workspace)

    first = runner.invoke(app, ["approve", release.id, "--by", "human:revisora", "-w", str(workspace)])
    assert first.exit_code == 0, first.output
    assert "1 de 2 votos" in first.output

    approvals = runner.invoke(app, ["approvals", release.id, "-w", str(workspace)])
    assert approvals.exit_code == 0
    assert "revisora" in approvals.output

    second = runner.invoke(app, ["approve", release.id, "--by", "human:auditora", "-w", str(workspace)])
    assert second.exit_code == 0, second.output
    assert "aprovado por" in second.output


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def test_api_signature_and_quorum_routes(runtime):
    from fastapi.testclient import TestClient

    from egr.api.server import create_app

    runtime.keystore.create()
    release = _submitted(runtime.release_manager, target="production")
    client = TestClient(create_app(runtime))

    signed = client.post(f"/v1/release/{release.id}/sign", json={"by": "human:revisora"})
    assert signed.status_code == 200
    assert signed.json()["assinatura"]["assinado_por"] == "human:revisora"

    verified = client.get(f"/v1/release/{release.id}/verify")
    assert verified.status_code == 200
    assert verified.json()["válida"] is True
    assert verified.json()["quórum"]["exigido"] == 2

    voted = client.post(f"/v1/release/{release.id}/approve", json={"by": "human:auditora", "note": "ok"})
    assert voted.status_code == 200, voted.text

    approvals = client.get(f"/v1/release/{release.id}/approvals")
    assert approvals.status_code == 200
    assert approvals.json()["quórum"]["aprovadores"] == ["auditora"]
    assert [item["ator"] for item in approvals.json()["votos"]] == ["auditora"]

    missing = client.get("/v1/release/nao-existe/approvals")
    assert missing.status_code == 404


# ----------------------------------------------------------------------
# configuração
# ----------------------------------------------------------------------
def test_release_policy_has_sane_defaults():
    config = EGRConfig()

    assert config.release.min_approvals == 1
    assert config.release.min_approvals_production == 2
    assert config.release.signature_environments == ["production"]
    assert config.release.allow_self_approval is False
    assert "release" in dump_config(config)
