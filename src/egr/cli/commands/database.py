"""Lacuna 6b: gatilhos de banco — o dado muda, o Runtime fica sabendo.

O gatilho é declarado aqui e criado de verdade no SQLite. O `when` não é SQL
livre: só colunas da lista branca da tabela, sob risco de recusa com motivo.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, kv, success, table, warning

app = typer.Typer(no_args_is_help=True, help="Gatilhos de banco: a linha muda, o Runtime reage")


@app.command(name="triggers")
def triggers(workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Gatilhos declarados e o tamanho da fila do que o banco avisou."""

    runtime = get_runtime(workspace)
    state = runtime.db_trigger_manager.status()
    if not state["gatilhos"]:
        info("nenhum gatilho declarado")
    else:
        table(
            "Gatilhos",
            ["id", "nome", "tabela", "evento", "quando", "emite", "ativa"],
            [
                [
                    item["id"],
                    item["nome"],
                    item["tabela"],
                    item["evento"],
                    item["quando"],
                    item["emite"],
                    "sim" if item["ativa"] else "não",
                ]
                for item in state["gatilhos"]
            ],
        )
    kv("Fila", {"pendente": state["fila"], "processados": state["processados"]})
    info("tabelas permitidas: " + ", ".join(state["tabelas_permitidas"]))


@app.command(name="trigger-add")
def add(
    name: str = typer.Argument(..., help="nome do gatilho"),
    on: str = typer.Option(..., "--on", help="tabela (lista branca)"),
    event: str = typer.Option("insert", "--event", help="insert | update | delete"),
    when: str = typer.Option("", "--when", help="condição: NEW.status = 'failed'"),
    emit: str = typer.Option(..., "--emit", help="evento do Runtime (ex.: db.task_failed)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Declara e instala um gatilho (recusa expressão fora da lista branca)."""

    from ...core.errors import ConfigError
    from ...core.ids import new_id
    from ...domain.coordination import DatabaseTrigger

    runtime = get_runtime(workspace)
    trigger = DatabaseTrigger(
        id=new_id("trg"),
        name=name,
        table=on,
        event=event,
        when=when,
        emit=emit,
    )
    try:
        saved = runtime.db_trigger_manager.install(trigger, actor="human:cli")
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"gatilho {saved.id} instalado em {saved.table}")
    info(f"emite {saved.emit} quando: {saved.when or 'qualquer mudança'}")


@app.command(name="trigger-remove")
def remove(
    trigger_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Remove o gatilho (e o SQL correspondente no banco)."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        runtime.db_trigger_manager.uninstall(trigger_id, actor="human:cli")
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"gatilho {trigger_id} removido")


@app.command(name="events")
def events(
    limit: int = typer.Option(20, "--limit", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """O que o banco avisou, e se o Runtime já viu."""

    runtime = get_runtime(workspace)
    rows = runtime.db_trigger_manager.events(limit=limit)
    if not rows:
        info("nenhum evento de banco")
        return
    table(
        "Eventos de banco",
        ["id", "tabela", "emite", "linha", "processado"],
        [
            [
                item.id,
                item.table,
                item.event,
                item.row_id or "-",
                item.processed_at.isoformat() if item.processed_at else "pendente",
            ]
            for item in rows
        ],
    )


@app.command(name="drain")
def drain(
    limit: int = typer.Option(50, "--limit", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Transforma o que o banco avisou em evento do Runtime (dispara workflows)."""

    runtime = get_runtime(workspace)
    processed = runtime.db_trigger_manager.drain(limit=limit)
    if not processed:
        info("fila vazia")
        return
    for item in processed:
        info(f"{item.table} → {item.event} ({item.row_id or '-'})")
    success(f"{len(processed)} evento(s) de banco emitido(s)")
    if runtime.db_events.count(pending_only=True):
        warning(f"{runtime.db_events.count(pending_only=True)} ainda na fila")
