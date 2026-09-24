"""`egr tui` — a interface completa no terminal.

Sem servidor, sem console web: é o mesmo Runtime, comandado por um painel.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error

app = typer.Typer(help="Interface completa no terminal (sem servidor)")


@app.command(name="ui")
def ui(
    workspace: Path = typer.Option(None, "--workspace", "-w", help="Workspace EGR"),
    env: str = typer.Option(None, "--env", help="development | staging | production"),
) -> None:
    """Abre a interface: objetivo em linguagem natural, comandos e configuração."""

    try:
        from ...tui import run_tui
    except ModuleNotFoundError as exc:  # textual é um extra opcional
        error('a interface precisa do extra opcional: pip install ".[tui]"')
        raise typer.Exit(code=2) from exc

    instance = get_runtime(workspace)
    target = Path(workspace) if workspace else instance.settings.workspace
    run_tui(target, env)
