"""EGR CLI — a primeira interface do Runtime é o terminal."""

from __future__ import annotations

from pathlib import Path

import typer

from ..version import MILESTONE, PHASE, __version__
from .commands import (
    agents,
    approvals,
    audits,
    database,
    dev,
    evaluation,
    gateway,
    governance,
    integration,
    mcp,
    memory,
    models,
    pack,
    policies,
    proposals,
    releases,
    security,
    tasks,
    tools,
    workflows,
    workspace,
)
from .context import get_runtime
from .formatting import info, table

app = typer.Typer(
    no_args_is_help=True,
    help=(
        "Enterprise AGI Runtime — o modelo pensa, o Runtime governa, "
        "as ferramentas executam, a memória pertence à empresa."
    ),
)

# root commands
app.command(name="init", help="Cria um workspace EGR")(workspace.init)
app.command(name="status", help="Estado do Runtime")(workspace.status)
app.command(name="doctor", help="Diagnóstico de saúde")(workspace.doctor)
app.command(name="serve", help="Sobe a API local + console")(workspace.serve)

# sub-applications
app.add_typer(tasks.app, name="task")
app.add_typer(agents.app, name="agent")
app.add_typer(tools.app, name="tool")
app.add_typer(policies.app, name="policy")
app.add_typer(models.app, name="model")
app.add_typer(memory.app, name="memory")
app.add_typer(approvals.app, name="approval")
app.add_typer(audits.app, name="audit")
app.add_typer(workflows.app, name="workflow")
# desenvolvimento (Fase 7): o Runtime estendendo a si mesmo sob proposta
app.add_typer(dev.app, name="dev")
# avaliação (Fase 8): provar qualidade, custo, latência e segurança
app.add_typer(evaluation.app, name="eval")
# canais (Fase 10): Telegram/Slack/Web sobre a mesma API
app.add_typer(gateway.app, name="gateway")
# integrações (Fase 11): REST/GraphQL/SQL/webhook com política e trilha
app.add_typer(integration.app, name="integration")
# pacotes verticais (Fase 12): catálogo instalado por proposta
app.add_typer(pack.app, name="pack")
app.add_typer(mcp.app, name="mcp")
app.add_typer(proposals.app, name="proposal")
# governança de produção (Fase 9): promoção com versão, evidência e volta
app.add_typer(releases.app, name="release")
app.add_typer(database.app, name="db")
app.add_typer(governance.app, name="lifecycle")
# segurança (Fase 4): identidade, RBAC, cofre e chaves
app.add_typer(security.app, name="security")
app.add_typer(security.identity_app, name="identity")
app.add_typer(security.secret_app, name="secret")
app.add_typer(security.key_app, name="key")


@app.command(name="version")
def version():
    """Versão e fase atual do Runtime."""

    info(f"EGR v{__version__}")
    info(PHASE)
    info(MILESTONE)


@app.command(name="logs")
def logs(
    limit: int = typer.Option(30, "--limit", "-l"),
    task: str = typer.Option(None, "--task", "-t"),
    workspace_path: Path = typer.Option(None, "--workspace", "-w"),
):
    """Atalho para a trilha de auditoria."""

    runtime = get_runtime(workspace_path)
    events = runtime.audit.list(task_id=task, limit=limit)
    if not events:
        info("nenhum evento registrado")
        return
    table(
        "Eventos",
        ["seq", "quando", "evento", "ator", "task", "detalhe"],
        [
            [
                event.seq,
                event.created_at.strftime("%H:%M:%S") if event.created_at else "-",
                event.type,
                event.actor,
                event.task_id or "-",
                str(event.payload)[:80],
            ]
            for event in reversed(events)
        ],
    )


if __name__ == "__main__":  # pragma: no cover
    app()
