"""Fase 12 — Vertical Packs.

Pack é atalho para começar, não para governar: entra por proposta verificada,
precisa de aprovação humana e só então escreve no workspace.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from egr.api.server import create_app
from egr.cli.commands.pack import app as pack_app
from egr.core.config import EGRConfig, Settings
from egr.core.errors import ConfigError
from egr.domain.enums import EventType, PackStatus, ProposalKind, ProposalStatus
from egr.domain.pack import Pack, PackRequirements
from egr.packs.catalog import available_packs, load_pack_dir, load_pack_file
from egr.runtime.runtime import Runtime
from egr.templates import render_workspace

BUILTINS = {"finance", "accounting", "sales", "operations", "hr", "marketing", "support"}


def make_runtime(tmp_path: Path) -> Runtime:
    render_workspace(tmp_path, sample=False)
    return Runtime(Settings(workspace=tmp_path, config=EGRConfig()), enable_logging=False)


def install(runtime: Runtime, pack_id: str, *, actor: str = "human:cli") -> str:
    """Propõe, aprova e aplica — o caminho real, sem atalho."""

    proposal = runtime.packs.propose(pack_id, actor=actor)
    runtime.workbench.approve(proposal.id, actor=actor)
    runtime.workbench.apply(proposal.id, actor=actor)
    return proposal.id


# --------------------------------------------------------------------------- #
# catálogo
# --------------------------------------------------------------------------- #
def test_builtin_catalog_has_seven_verticals(runtime):
    ids = {pack.id for pack in runtime.packs.available()}

    assert ids == BUILTINS


def test_every_pack_declares_content_and_requirements(runtime):
    for pack in runtime.packs.available():
        assert pack.total >= 5, f"{pack.id} sem conteúdo suficiente"
        assert pack.version
        assert pack.title
        assert pack.checksum()


def test_catalog_packs_are_valid_artifacts(runtime):
    """Cada artefato do pack precisa sobreviver ao loader do próprio Runtime."""

    from egr.domain.agent import AgentSpec
    from egr.domain.evaluation import EvaluationSuite
    from egr.domain.integration import Integration
    from egr.domain.policy import Policy
    from egr.domain.workflow import Workflow

    validators = {
        "agents": AgentSpec,
        "workflows": Workflow,
        "policies": Policy,
        "integrations": Integration,
        "evaluations": EvaluationSuite,
    }
    for pack in runtime.packs.available():
        for kind, model in validators.items():
            for item in getattr(pack, kind):
                model.model_validate(item)  # levanta se o pack estiver mentindo


def test_pack_requirements_defaults():
    pack = Pack(id="x", agents=[{"id": "a"}])

    assert pack.total == 1
    assert pack.requires.min_environment == "development"
    assert pack.documents == {}


def test_workspace_pack_overrides_builtin(runtime):
    local = runtime.settings.workspace / "packs"
    local.mkdir(parents=True, exist_ok=True)
    (local / "support.yaml").write_text(
        "id: support\nname: Suporte do time\nversion: 2.0.0\nagents:\n  - id: support-agent\n",
        encoding="utf-8",
    )

    packs = {pack.id: pack for pack in runtime.packs.available()}

    assert packs["support"].version == "2.0.0"
    assert packs["support"].origin == "workspace"


def test_broken_pack_file_raises(tmp_path):
    (tmp_path / "ruim.yaml").write_text("id: [sem fechar\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_pack_file(tmp_path / "ruim.yaml")


def test_unknown_pack_raises(runtime):
    with pytest.raises(ConfigError):
        runtime.packs.get("nao-existe")


def test_load_pack_dir_missing(tmp_path):
    assert load_pack_dir(tmp_path / "nada") == []


# --------------------------------------------------------------------------- #
# verificação
# --------------------------------------------------------------------------- #
def test_check_finds_missing_requirement(runtime):
    pack = Pack(id="x", requires=PackRequirements(tools=["nao.existe"]), agents=[{"id": "a"}])

    checks = {item["nome"]: item for item in runtime.packs.check(pack)}

    assert checks["ferramenta:nao.existe"]["ok"] is False
    assert checks["ferramenta:nao.existe"]["nível"] == "error"


def test_check_satisfied_requirement_is_info_not_error(runtime):
    pack = Pack(id="x", requires=PackRequirements(tools=["database.query"]), agents=[{"id": "a"}])

    checks = {item["nome"]: item for item in runtime.packs.check(pack)}
    item = checks["ferramenta:database.query"]

    assert item["ok"] is True
    assert item["nível"] == "info"
    assert item["detalhe"] == ""


def test_check_flags_collision_as_warning(runtime):
    (runtime.settings.workspace / "agents").mkdir(parents=True, exist_ok=True)
    (runtime.settings.workspace / "agents" / "finance-agent.yaml").write_text("id: finance-agent\n", encoding="utf-8")
    pack = Pack(id="x", agents=[{"id": "finance-agent"}])

    checks = {item["nome"]: item for item in runtime.packs.check(pack)}

    assert checks["agents:finance-agent"]["nível"] == "warning"


def test_check_environment_minimum(tmp_path):
    runtime = make_runtime(tmp_path / "ws")
    runtime.settings.config.environment = "development"
    pack = Pack(id="x", requires=PackRequirements(min_environment="production"), agents=[{"id": "a"}])

    checks = {item["nome"]: item for item in runtime.packs.check(pack)}

    assert checks["ambiente"]["ok"] is False
    runtime.close()


def test_check_empty_pack_has_no_artifacts(runtime):
    checks = {item["nome"]: item for item in runtime.packs.check(Pack(id="vazio"))}

    assert checks["conteúdo"]["ok"] is False


# --------------------------------------------------------------------------- #
# instalação: proposta, aprovação e aplicação
# --------------------------------------------------------------------------- #
def test_install_denied_when_requirement_missing(runtime):
    with pytest.raises(ConfigError) as exc:
        runtime.packs.propose("nao-existe")

    assert "não encontrado" in str(exc.value)


def test_propose_creates_proposal_and_writes_nothing(runtime):
    proposal = runtime.packs.propose("finance", actor="human:vitor")

    assert str(proposal.kind) == ProposalKind.PACK
    assert proposal.name == "finance"
    assert proposal.target == "packs/finance.yaml"
    assert not (runtime.settings.workspace / "packs" / "finance.yaml").exists()
    assert not (runtime.settings.workspace / "agents" / "cashflow-agent.yaml").exists()


def test_proposal_is_validated_by_the_workbench(runtime):
    proposal = runtime.packs.propose("sales")

    assert proposal.status in (ProposalStatus.VALIDATED, ProposalStatus.FAILED)
    assert proposal.valid is True


def test_apply_materializes_every_artifact(runtime):
    proposal_id = install(runtime, "finance")

    workspace = runtime.settings.workspace
    assert (workspace / "agents" / "cashflow-agent.yaml").exists()
    assert (workspace / "workflows" / "fechamento-caixa.yaml").exists()
    assert (workspace / "policies" / "tesouraria-pagamento.yaml").exists()
    assert (workspace / "integrations" / "BANCO.yaml").exists()
    assert (workspace / "evaluations" / "finance-fechamento.yaml").exists()
    assert (workspace / "documents" / "politica-caixa.md").exists()
    assert (workspace / "packs" / "finance.yaml").exists()

    record = runtime.packs_repository.get("finance")
    assert record is not None
    assert record.proposal == proposal_id
    assert record.installed_by == "human:cli"
    assert len(record.files) == 7


def test_applied_artifacts_are_loaded_by_the_runtime(runtime):
    install(runtime, "finance")

    assert "cashflow-agent" in runtime.agents
    assert "fechamento-caixa" in runtime.workflows
    assert "finance-fechamento" in runtime.evaluation_suites
    assert "BANCO" in runtime.connectors.registry
    policies = {item.id for item in runtime.policy.list_policies()}
    assert "tesouraria-pagamento" in policies


def test_install_is_recorded_in_the_ledger(runtime):
    install(runtime, "hr")

    tipos = [str(event.type) for event in runtime.audit.list(limit=300)]

    assert EventType.PACK_INSTALLED in tipos


def test_pack_status_reports_installed_and_available(runtime):
    install(runtime, "marketing")

    data = runtime.packs_status()
    estados = {item["id"]: item["status"] for item in data["pacotes"]}

    assert estados["marketing"] == str(PackStatus.INSTALLED)
    assert estados["sales"] == str(PackStatus.AVAILABLE)
    assert data["instalados"] == 1
    assert data["catálogo"]["total"] >= 7


def test_outdated_pack_is_flagged(runtime):
    install(runtime, "operations")
    record = runtime.packs_repository.get("operations")
    record.version = "0.0.1"
    runtime.packs_repository.save(record)

    estados = {item["id"]: item["status"] for item in runtime.packs_status()["pacotes"]}

    assert estados["operations"] == str(PackStatus.OUTDATED)


def test_health_flags_outdated_pack(runtime):
    install(runtime, "operations")
    record = runtime.packs_repository.get("operations")
    record.version = "0.0.1"
    runtime.packs_repository.save(record)

    checks = {item["check"]: item for item in runtime.health()["checks"]}

    assert checks["pack:operations"]["ok"] is False


def test_reinstalling_twice_is_idempotent(runtime):
    install(runtime, "support")
    install(runtime, "support")

    assert runtime.packs_repository.count() == 1
    assert len(runtime.packs_repository.get("support").files) == len(runtime.packs.plan(runtime.packs.get("support")))


def test_materialize_requires_approved_proposal(runtime):
    """Nada chega ao workspace sem passar pela Fase 7."""

    proposal = runtime.packs.propose("support")

    with pytest.raises(ConfigError):
        runtime.workbench.apply(proposal.id, actor="human:cli")


# --------------------------------------------------------------------------- #
# remoção
# --------------------------------------------------------------------------- #
def test_remove_deletes_files_written_by_the_pack(runtime):
    install(runtime, "sales")

    resultado = runtime.packs.remove("sales", actor="human:vitor")

    assert resultado["mantidos"] == []
    assert not (runtime.settings.workspace / "agents" / "sales-agent.yaml").exists()
    assert runtime.packs_repository.get("sales") is None


def test_remove_keeps_files_edited_after_install(runtime):
    install(runtime, "sales")
    policy = runtime.settings.workspace / "policies" / "vendas-desconto.yaml"
    policy.write_text(policy.read_text(encoding="utf-8") + "\n# ajuste do time\n", encoding="utf-8")

    resultado = runtime.packs.remove("sales")

    assert policy.exists()
    assert any("vendas-desconto" in item for item in resultado["mantidos"])


def test_remove_unknown_pack_raises(runtime):
    with pytest.raises(ConfigError):
        runtime.packs.remove("nao-instalado")


def test_removal_is_recorded(runtime):
    install(runtime, "support")
    runtime.packs.remove("support")

    tipos = [str(event.type) for event in runtime.audit.list(limit=300)]

    assert EventType.PACK_REMOVED in tipos


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_lists_catalog(tmp_path):
    runtime = make_runtime(tmp_path)
    runner = CliRunner()

    result = runner.invoke(pack_app, ["list", "-w", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "finance" in result.output
    runtime.close()


def test_cli_show_details_the_pack(tmp_path):
    runtime = make_runtime(tmp_path)
    runner = CliRunner()

    result = runner.invoke(pack_app, ["show", "finance", "-w", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "cashflow-agent" in result.output or "agentes" in result.output
    runtime.close()


def test_cli_check_blocks_broken_pack(tmp_path):
    runtime = make_runtime(tmp_path)
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "quebrado.yaml").write_text(
        "id: quebrado\nname: Quebrado\nrequires:\n  tools: [nao.existe]\nagents:\n  - id: a\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(pack_app, ["check", "quebrado", "-w", str(tmp_path)])

    assert result.exit_code == 1
    assert "impedimento" in result.output
    runtime.close()


def test_cli_install_prints_the_next_steps(tmp_path):
    runtime = make_runtime(tmp_path)
    runner = CliRunner()

    result = runner.invoke(pack_app, ["install", "hr", "--by", "human:vitor", "-w", str(tmp_path)])
    saida = result.output

    assert result.exit_code == 0, saida
    assert "proposta" in saida
    assert "egr proposal approve" in saida
    runtime.close()


def test_cli_status_and_add(tmp_path):
    runtime = make_runtime(tmp_path)
    runner = CliRunner()

    vazio = runner.invoke(pack_app, ["status", "-w", str(tmp_path)])
    assert vazio.exit_code == 0
    assert "nenhum pack instalado" in vazio.output

    externo = tmp_path / "meu-pack.yaml"
    externo.write_text("id: interno\nname: Pack do time\nagents:\n  - id: agente-interno\n", encoding="utf-8")
    add = runner.invoke(pack_app, ["add", str(externo), "-w", str(tmp_path)])

    assert add.exit_code == 0, add.output
    assert "interno" in add.output
    assert (tmp_path / "packs" / "interno.yaml").exists()
    runtime.close()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def test_api_lists_and_checks_packs(runtime):
    client = TestClient(create_app(runtime))

    data = client.get("/v1/packs").json()
    assert data["catálogo"]["total"] >= 7

    detalhe = client.get("/v1/packs/finance").json()
    assert detalhe["id"] == "finance"
    assert any("agents/cashflow-agent.yaml" in item for item in detalhe["arquivos"])

    checks = client.get("/v1/packs/finance/check").json()
    assert all(item["ok"] for item in checks if item["nível"] == "error")


def test_api_install_creates_proposal(runtime):
    client = TestClient(create_app(runtime))

    response = client.post("/v1/packs/finance/install?by=human:api")

    assert response.status_code == 200
    assert response.json()["kind"] == "pack"
    assert response.json()["status"] == "validated"


def test_api_unknown_pack_is_404(runtime):
    client = TestClient(create_app(runtime))

    assert client.get("/v1/packs/nao-existe").status_code == 404


def test_api_remove_pack(runtime):
    install(runtime, "sales")
    client = TestClient(create_app(runtime))

    response = client.delete("/v1/packs/sales")

    assert response.status_code == 200
    assert response.json()["pack"] == "sales"


# --------------------------------------------------------------------------- #
# propostas (superfície que o pack expôs como quebrada)
# --------------------------------------------------------------------------- #
def test_proposal_cli_delegations_exist(runtime):
    """`egr proposal *` chamava métodos inexistentes no Runtime (corrigido)."""

    agente = (
        "id: agente-teste\n"
        "name: Agente de teste\n"
        "objective: Verificar a delegação do CLI de propostas\n"
        "model:\n  capability: reasoning\n"
        "permissions:\n  tools: []\n  namespaces: [default]\n"
    )
    proposal = runtime.dev_propose("agent", "agente-teste", agente, "teste")
    assert runtime.dev_verify(proposal.id).status == ProposalStatus.VALIDATED
    assert runtime.dev_approve(proposal.id, "human:cli").status == ProposalStatus.APPROVED
    assert runtime.dev_apply(proposal.id, actor="human:cli")["status"] == "applied"
    assert runtime.dev_show(proposal.id).id == proposal.id
    assert any(item.id == proposal.id for item in runtime.dev_list(limit=10))


def test_dev_prove_refuses_pack(runtime):
    proposal = runtime.packs.propose("support")

    with pytest.raises(ConfigError) as exc:
        runtime.dev_prove(proposal.id)

    assert "pack não tem prova" in str(exc.value)


def test_dev_prove_attaches_evidence(runtime):
    """Prova o que já está no workspace: o agente de documentos existe."""

    conteudo = (runtime.settings.workspace / "agents" / "document-agent.yaml").read_text(encoding="utf-8")
    proposal = runtime.dev_propose("agent", "document-agent", conteudo, "prova")

    provada, run = runtime.dev_prove(proposal.id)

    assert provada.metadata["evidence_run"] == run.id
    assert provada.status in (ProposalStatus.TESTED, ProposalStatus.FAILED)


def test_dev_prove_refuses_artifact_that_does_not_exist_yet(runtime):
    proposal = runtime.dev_propose(
        "policy",
        "politica-fantasma",
        json.dumps({"id": "politica-fantasma", "rules": [{"id": "r1", "action": "x", "decision": "deny"}]}),
        "teste",
    )

    with pytest.raises(ConfigError) as exc:
        runtime.dev_prove(proposal.id)

    assert "ainda não está no workspace" in str(exc.value)


# --------------------------------------------------------------------------- #
# serviço isolado
# --------------------------------------------------------------------------- #
def test_available_packs_accepts_missing_workspace():
    packs = available_packs(Path("/tmp/egr-nao-existe"))

    assert {pack.id for pack in packs} >= BUILTINS
