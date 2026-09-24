"""egr audit show|verify|stats|export|verify-export · rastreabilidade empresarial."""

from __future__ import annotations

import hashlib
from pathlib import Path

import typer

from ...audit.export import (
    build_manifest,
    event_dict,
    to_csv,
    to_jsonl,
    verify_export_file,
)
from ...domain.enums import EventType
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


@app.command(name="export")
def export_trail(
    format: str = typer.Option("jsonl", "--format", "-f", help="jsonl ou csv"),
    task_id: str = typer.Option(None, "--task", "-t"),
    type: str = typer.Option(None, "--type", help="Filtro por tipo de evento"),
    actor: str = typer.Option(None, "--actor", help="Filtro por ator"),
    since: str = typer.Option(None, "--since", help="created_at inicial (ISO-8601)"),
    until: str = typer.Option(None, "--until", help="created_at final (ISO-8601)"),
    out: Path = typer.Option(None, "--out", "-o", help="Arquivo de saída (padrão: stdout)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Exporta a trilha com manifesto verificável (pacote de evidência)."""

    if format not in ("jsonl", "csv"):
        error("formato deve ser 'jsonl' ou 'csv'")
        raise typer.Exit(code=2)
    runtime = get_runtime(workspace)
    events = [
        event_dict(event)
        for event in runtime.audit.scan(
            task_id=task_id, type=type, actor=actor, since=since, until=until
        )
    ]
    head_row = runtime.db.query_one("SELECT hash FROM events ORDER BY seq DESC LIMIT 1")
    head = head_row["hash"] if head_row else None
    manifest = build_manifest(
        events,
        format=format,
        filters={"task_id": task_id, "type": type, "actor": actor, "since": since, "until": until},
        ledger_head=head,
    )
    content = to_jsonl(manifest, events) if format == "jsonl" else to_csv(manifest, events)
    if out is None:
        typer.echo(content, nl=False)
        target, digest = "stdout", None
    else:
        out.write_text(content, encoding="utf-8")
        target = str(out)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # A exportação também é auditada: quem exportou o quê, quando, com qual sha.
    runtime.audit.record(
        EventType.AUDIT_EXPORTED,
        actor="cli",
        environment=str(runtime.settings.environment),
        payload={
            "target": target,
            "sha256": digest,
            "bytes": len(content.encode("utf-8")),
            "format": format,
            "events": len(events),
            "first_seq": manifest["first_seq"],
            "last_seq": manifest["last_seq"],
            "head": head,
            "filters": manifest["filters"],
        },
    )
    if out is not None:
        success(f"trilha exportada: {out} ({len(events)} eventos)")


@app.command(name="verify-export")
def verify_export(
    file: Path = typer.Argument(..., help="Arquivo gerado por `egr audit export`"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Verifica um pacote de evidência sem precisar do banco."""

    if not file.exists():
        error(f"arquivo não encontrado: {file}")
        raise typer.Exit(code=2)
    result = verify_export_file(file)
    if as_json:
        json_output(result)
        raise typer.Exit(code=0 if result["valid"] else 1)
    kv(
        "Pacote de evidência",
        {
            "eventos": result["events"],
            "faixa": f"{result['first_seq']}..{result['last_seq']}",
            "válido": "sim" if result["valid"] else "NÃO",
            "completo": "sim" if result.get("complete") else "não",
            "problemas": len(result["broken"]),
        },
    )
    if not result["valid"]:
        error("pacote adulterado: " + str(result["broken"][:3]))
        raise typer.Exit(code=1)
    success("pacote de evidência íntegro")


__all__ = ["app"]
