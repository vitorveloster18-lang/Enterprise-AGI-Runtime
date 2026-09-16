"""egr memory search|write|list|stats · a memória pertence à empresa."""

from __future__ import annotations

from pathlib import Path

import typer

from ...domain.enums import MemoryKind
from ..context import get_runtime
from ..formatting import info, json_output, kv, success, table

app = typer.Typer(help="Memória: knowledge · operational · episodic · semantic")


@app.command(name="search")
def search(
    query: str = typer.Argument(..., help="Texto a recuperar"),
    namespace: str = typer.Option("", "--namespace", "-n"),
    kind: str = typer.Option("", "--kind", "-k", help="knowledge|operational|episodic|semantic"),
    limit: int = typer.Option(5, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Busca na memória da empresa."""

    runtime = get_runtime(workspace)
    records = runtime.memory.search(
        query,
        namespaces=[namespace] if namespace else None,
        kinds=[kind] if kind else None,
        limit=limit,
    )
    if as_json:
        json_output([record.model_dump(mode="json") for record in records])
        return
    if not records:
        info("nenhum registro encontrado")
        return
    table(
        "Memória",
        ["id", "namespace", "tipo", "score", "resumo"],
        [
            [record.id, record.namespace, record.kind, record.score, (record.summary or record.content)[:70]]
            for record in records
        ],
    )


@app.command(name="write")
def write(
    content: str = typer.Argument(..., help="Conteúdo a memorizar"),
    kind: str = typer.Option("knowledge", "--kind", "-k"),
    namespace: str = typer.Option("default", "--namespace", "-n"),
    tags: str = typer.Option("", "--tags", "-t", help="Separadas por vírgula"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Escreve um registro de memória (normalmente feito pelo Runtime)."""

    runtime = get_runtime(workspace)
    record = runtime.memory.write(
        content,
        kind=MemoryKind(kind),
        namespace=namespace,
        tags=[item.strip() for item in tags.split(",") if item.strip()],
        source="cli",
    )
    success(f"memória registrada: {record.id}")


@app.command(name="list")
def list_memory(
    namespace: str = typer.Option(None, "--namespace", "-n"),
    limit: int = typer.Option(20, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista registros recentes."""

    runtime = get_runtime(workspace)
    records = runtime.memory.list(namespace=namespace, limit=limit)
    if as_json:
        json_output([record.model_dump(mode="json") for record in records])
        return
    if not records:
        info("memória vazia")
        return
    table(
        "Memória recente",
        ["id", "namespace", "tipo", "origem", "resumo", "criado em"],
        [
            [
                record.id,
                record.namespace,
                record.kind,
                record.source,
                (record.summary or record.content)[:60],
                record.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
            for record in records
        ],
    )


@app.command(name="stats")
def stats(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Estatísticas da memória."""

    runtime = get_runtime(workspace)
    data = runtime.memory.stats()
    if as_json:
        json_output(data)
        return
    kv("Memória", {"total": data["total"], **data["by_kind"]})


__all__ = ["app"]
