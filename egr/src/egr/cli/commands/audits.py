"""egr audit show|verify|stats · rastreabilidade empresarial."""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table

app = typer.Typer(help="Auditoria: ledger append-only com hash encadeado")


@app.command(name="show")
def show(
    task_id: str = typer.Option(None, "--task", "-t"),
    type: str = typer.Option(None, "--type", help="Filtro por tipo de evento"),
    limit: int = typer.Option(30, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Mostra eventos de auditoria."""

    runtime = get_runtime(workspace)
    events = runtime.audit.list(task_id=task_id, type=type, limit=limit)
    if as_json:
        json_output([event.model_dump(mode="json") for event in events])
        return
    if not events:
        info("nenhum evento registrado")
        return
    table(
        "Auditoria",
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


@app.command(name="verify")
def verify(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Verifica a integridade da cadeia de hash do ledger."""

    runtime = get_runtime(workspace)
    result = runtime.audit.verify()
    if as_json:
        json_output(result)
        raise typer.Exit(code=0 if result["valid"] else 1)
    kv(
        "Auditoria",
        {
            "eventos": result["events"],
            "cadeia válida": "sim" if result["valid"] else "NÃO",
            "head": result["head"][:16] + "...",
            "registros quebrados": len(result["broken"]),
        },
    )
    if not result["valid"]:
        error("ledger adulterado: " + str(result["broken"][:3]))
        raise typer.Exit(code=1)
    success("cadeia de auditoria íntegra")


@app.command(name="stats")
def stats(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Contagem de eventos por tipo."""

    runtime = get_runtime(workspace)
    rows = runtime.db.query(
        "SELECT type, COUNT(*) AS total FROM events GROUP BY type ORDER BY total DESC"
    )
    data = {row["type"]: row["total"] for row in rows}
    if as_json:
        json_output(data)
        return
    table("Eventos", ["tipo", "total"], [[key, value] for key, value in data.items()])


__all__ = ["app"]
