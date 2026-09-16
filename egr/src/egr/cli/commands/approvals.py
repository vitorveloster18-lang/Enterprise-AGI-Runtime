"""egr approval list|approve|deny|show · o humano no loop."""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table, warning

app = typer.Typer(help="Aprovações: objeto de primeira classe do Runtime")


@app.command(name="list")
def list_approvals(
    status: str = typer.Option(None, "--status", "-s", help="pending|approved|denied"),
    limit: int = typer.Option(20, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista aprovações."""

    runtime = get_runtime(workspace)
    approvals = runtime.approvals.list(status=status, limit=limit)
    if as_json:
        json_output([approval.model_dump(mode="json") for approval in approvals])
        return
    if not approvals:
        info("nenhuma aprovação")
        return
    table(
        "Aprovações",
        ["id", "status", "ação", "solicitado por", "papel exigido", "task", "criado em"],
        [
            [
                approval.id,
                approval.status,
                approval.tool,
                approval.requested_by,
                approval.required_role or "-",
                approval.task_id or "-",
                approval.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            ]
            for approval in approvals
        ],
    )
    pending = [approval for approval in approvals if approval.pending]
    if pending:
        warning(f"{len(pending)} aguardando decisão humana")


@app.command(name="show")
def show(
    approval_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Mostra uma aprovação."""

    runtime = get_runtime(workspace)
    approval = runtime.approvals.get(approval_id)
    if approval is None:
        error(f"aprovação {approval_id} não encontrada")
        raise typer.Exit(code=1)
    if as_json:
        json_output(approval.model_dump(mode="json"))
        return
    kv(
        f"Aprovação {approval.id}",
        {
            "ação": approval.action,
            "ferramenta": approval.tool,
            "argumentos (redigidos)": str(approval.args),
            "solicitado por": approval.requested_by,
            "task": approval.task_id or "-",
            "ambiente": approval.environment,
            "papel exigido": approval.required_role or "-",
            "status": approval.status,
            "motivo": approval.reason,
            "decidido por": approval.decided_by or "-",
        },
    )


@app.command(name="approve")
def approve(
    approval_id: str = typer.Argument(...),
    by: str = typer.Option("human", "--by", help="Quem está aprovando"),
    note: str = typer.Option("", "--note"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Aprova uma ação (e retoma a task, se houver)."""

    runtime = get_runtime(workspace)
    try:
        task = runtime.approve(approval_id, decided_by=by, note=note or None)
    except KeyError as exc:
        error(f"aprovação {approval_id} não encontrada")
        raise typer.Exit(code=1) from exc
    success(f"aprovação {approval_id} registrada")
    if task is not None:
        info(f"task {task.id} → {task.status}")


@app.command(name="deny")
def deny(
    approval_id: str = typer.Argument(...),
    by: str = typer.Option("human", "--by"),
    note: str = typer.Option("", "--note"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Nega uma ação e continua a task sem ela."""

    runtime = get_runtime(workspace)
    try:
        task = runtime.deny(approval_id, decided_by=by, note=note or None)
    except KeyError as exc:
        error(f"aprovação {approval_id} não encontrada")
        raise typer.Exit(code=1) from exc
    success(f"aprovação {approval_id} negada")
    if task is not None:
        info(f"task {task.id} → {task.status}")


__all__ = ["app"]
