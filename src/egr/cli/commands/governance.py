"""Comandos do ciclo de vida dev → staging → produção (Fases 7-9).

Estes comandos já existem na superfície do CLI, mas dependem de módulos que
ainda serão construídos. Eles deixam o caminho explícito em vez de inventar
mágica: a promoção para produção nunca é automática.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import info, kv, panel, warning

app = typer.Typer(help="Ciclo de vida: dev → staging → proposta → humano → produção")


@app.command(name="dev")
def dev(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Mostra o ambiente de desenvolvimento dos agentes (sandbox isolado)."""

    runtime = get_runtime(workspace)
    kv(
        "Development environment",
        {
            "workspace": str(runtime.settings.workspace),
            "sandbox": str(runtime.settings.sandbox_path),
            "artifacts": str(runtime.settings.artifacts_path),
            "python_exec": runtime.settings.config.security.python_exec_enabled,
            "max_steps": runtime.settings.config.runtime.max_steps,
            "ferramentas": len(runtime.tools.list()),
            "agentes": len(runtime.agents),
            "modo do sandbox": f"{runtime.sandbox_info['mode']} (configurado: {runtime.sandbox_info['configured']})",
            "imagem do sandbox": runtime.sandbox_info["image"],
            "rede no sandbox": runtime.sandbox_info["network"],
        },
    )
    info("no Development o agente pode inspecionar, criar, testar e iterar — sempre isolado")


@app.command(name="stage")
def stage(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Status do ambiente de staging (testes, simulação, avaliação)."""

    runtime = get_runtime(workspace)
    tasks = runtime.tasks.list(environment="staging", limit=10)
    kv(
        "Staging",
        {
            "ambiente atual": str(runtime.settings.environment),
            "tasks em staging": len(tasks),
            "critério": "todo artefato precisa de testes + avaliação antes de virar proposta",
        },
    )
    warning("execução completa de staging (testes/simulação/métricas) entra na Fase 8")


@app.command(name="list")
def list_proposals(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Lista propostas de promoção (Fase 9)."""

    runtime = get_runtime(workspace)
    pending = runtime.approvals.list(status="pending", limit=20)
    if not pending:
        info("nenhuma proposta de promoção pendente")
        return
    for approval in pending:
        panel(
            f"Proposta {approval.id}",
            f"ação: {approval.action}\nsolicitado por: {approval.requested_by}\n"
            f"papel exigido: {approval.required_role or '-'}\nambiente: {approval.environment}",
            style="yellow",
        )


@app.command(name="deploy")
def deploy(
    target: str = typer.Option("production", "--target", "-t"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Promove uma versão (somente após aprovação humana — Fase 9)."""

    warning("deploy automático não existe no EGR: a promoção exige aprovação humana")
    panel(
        "Fluxo correto",
        "DEV → STAGING → PROPOSAL → HUMAN APPROVAL → PRODUCTION\n\n"
        f"alvo solicitado: {target}\n"
        "implementação completa: Fase 9 (Production Governance)",
        style="yellow",
    )


@app.command(name="rollback")
def rollback(
    version: str = typer.Argument("última", help="Versão alvo"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Reverte uma versão promovida (Fase 9)."""

    warning(f"rollback para '{version}' depende do versionamento de artefatos (Fase 9)")
    info("versionamento de agents/tools/workflows/policies já é parte do modelo de domínio")


__all__ = ["app"]
