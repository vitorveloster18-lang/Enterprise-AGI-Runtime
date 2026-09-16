"""EGR CLI — a primeira interface do Runtime é o terminal."""

from __future__ import annotations

from pathlib import Path

import typer

from ..version import MILESTONE, PHASE, __version__
from .commands import (
    agents,
    approvals,
    audits,
    governance,
    mcp,
    memory,
    models,
    policies,
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
app.add_typer(mcp.app, name="mcp")
app.add_typer(governance.app, name="proposal")
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
