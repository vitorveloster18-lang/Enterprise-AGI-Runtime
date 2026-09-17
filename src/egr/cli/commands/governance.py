"""Comandos do ciclo de vida dev → staging → produção (Fases 7-9).

O primeiro degrau (Development) está implementado na Fase 7: `egr proposal dev`
mostra o ambiente real e `egr dev ...` opera o ciclo de propostas. Staging
(Fase 8) e produção (Fase 9) seguem declarados como lacuna em vez de mágica:
a promoção para produção nunca é automática.
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
    """Ambiente de desenvolvimento: onde o trabalho novo nasce (Fase 7)."""

    runtime = get_runtime(workspace)
    data = runtime.dev_status()
    proposals = data["proposals"]
    info("ciclo: propor → verificar → provar (código) → aprovar → aplicar")
    kv(
        "Development environment",
        {
            "propostas": proposals["total"],
            "aguardando aprovação": proposals["awaiting_approval"],
            "por status": ", ".join(f"{k}={v}" for k, v in sorted(proposals["by_status"].items())) or "-",
            "ferramentas do workspace": ", ".join(data["workspace_tools"]["files"]) or "-",
            "sandbox": f"{data['sandbox']['mode']} (configurado: {data['sandbox']['configured']})",
            "agentes": data["agents"],
            "workflows": data["workflows"],
        },
    )
    info("em Development o agente pode inspecionar, propor e provar — sempre isolado e nunca aplicando")
    info("comandos: egr dev new | validate | test | diff | approve | apply")


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
