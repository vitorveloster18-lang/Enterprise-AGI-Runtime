"""A escada de ambientes: development → staging → produção (Fases 7-9).

O primeiro degrau (Development) é a Fase 7: o agente inspeciona, propõe e prova
sempre isolado. Staging e produção são a Fase 9: a promoção é um **release** com
snapshot, evidência e aprovação humana — nunca automática.

Comandos reais do ciclo:

    egr dev ...            proposta verificada e provada (Fase 7)
    egr eval ...           avaliação que vira evidência (Fase 8)
    egr release ...        promoção dev → staging → produção (Fase 9)
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import info, kv, warning

app = typer.Typer(help="Ciclo de vida: dev → staging → release → humano → produção")


@app.command(name="dev")
def dev(workspace: Path = typer.Option(None, "--workspace", "-w")):
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
    info("comandos: egr proposal create | verify | prove | approve | apply")


@app.command(name="stage")
def stage(workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Ambiente de staging: avaliação antes da promoção (Fase 8/9)."""

    runtime = get_runtime(workspace)
    data = runtime.evaluation_status()
    governance = runtime.governance_status()
    kv(
        "Staging",
        {
            "ambiente atual": str(runtime.settings.environment),
            "suítes": data["suites"]["total"],
            "execuções": data["runs"]["total"],
            "taxa de acerto": f"{data['runs']['avg_pass_rate']:.0%}" if data["runs"]["total"] else "-",
            "releases aplicados": ", ".join(f"{k}={len(v)}" for k, v in governance["deployed"].items()) or "-",
            "critério": "todo artefato precisa de avaliação aprovada antes de virar release",
        },
    )
    info("comandos: egr eval smoke|run <artefato> → egr release create ... --to staging")


@app.command(name="production")
def production(workspace: Path = typer.Option(None, "--workspace", "-w")):
    """O que está em produção e quem autorizou (Fase 9)."""

    runtime = get_runtime(workspace)
    data = runtime.governance_status()
    deployed = data["deployed"].get("production") or []
    kv(
        "Production",
        {
            "ambiente atual": str(runtime.settings.environment),
            "artefatos aplicados": ", ".join(deployed) or "nenhum",
            "releases": data["releases"]["total"],
            "por status": ", ".join(f"{k}={v}" for k, v in sorted(data["releases"]["by_status"].items())) or "-",
            "identidade exigida": data["governance"]["identity_required"],
            "papel mínimo": data["governance"]["approval_min_role"],
        },
    )
    if deployed:
        info("rollback: egr release versions <tipo> <nome> && egr release rollback <release>")
    else:
        warning("nada em produção — a promoção exige release aprovado: `egr release create ... --to production`")


__all__ = ["app"]
