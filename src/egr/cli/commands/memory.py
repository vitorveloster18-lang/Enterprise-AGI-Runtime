"""egr memory search|write|show|list|consolidate|reindex|forget|stats.

A memória pertence à empresa: recuperada por léxico + semântica, reforçada pelo
uso e consolidada por política — nunca cresce sem critério.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ...domain.enums import MemoryKind
from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table, warning

app = typer.Typer(help="Memória: knowledge · operational · episodic · semantic")

KINDS = [kind.value for kind in MemoryKind]


@app.command(name="search")
def search(
    query: str = typer.Argument(..., help="Texto a recuperar"),
    mode: str = typer.Option("", "--mode", "-m", help="hybrid | fts | semantic"),
    namespace: str = typer.Option("", "--namespace", "-n"),
    kind: str = typer.Option("", "--kind", "-k", help="knowledge|operational|episodic|semantic"),
    limit: int = typer.Option(5, "--limit", "-l"),
    archived: bool = typer.Option(False, "--archived", help="Incluir registros arquivados"),
    explain: bool = typer.Option(False, "--explain", help="Mostrar a fusão léxico/semântica"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Busca na memória da empresa (híbrido por padrão)."""

    runtime = get_runtime(workspace)
    records = runtime.memory.search(
        query,
        namespaces=[namespace] if namespace else None,
        kinds=[kind] if kind else None,
        limit=limit,
        mode=mode or None,
        include_archived=archived,
        explain=explain,
    )
    if as_json:
        json_output([record.model_dump(mode="json") for record in records])
        return
    if not records:
        info("nenhum registro encontrado")
        return
    columns = ["id", "namespace", "tipo", "score", "resumo"]
    if explain:
        columns += ["rank léxico", "rank semântico", "cosseno"]
    rows = []
    for record in records:
        trace = record.metadata.get("retrieval", {})
        row = [
            record.id,
            record.namespace,
            record.kind,
            f"{record.score:.4f}" if record.score is not None else "-",
            (record.summary or record.content)[:55],
        ]
        if explain:
            row += [
                trace.get("fts_rank") or "-",
                trace.get("semantic_rank") or "-",
                trace.get("cosine") if trace.get("cosine") is not None else "-",
            ]
        rows.append(row)
    table("Memória", columns, rows)


@app.command(name="write")
def write(
    content: str = typer.Argument(..., help="Conteúdo a memorizar"),
    kind: str = typer.Option("knowledge", "--kind", "-k"),
    namespace: str = typer.Option("default", "--namespace", "-n"),
    tags: str = typer.Option("", "--tags", "-t", help="Separadas por vírgula"),
    importance: float = typer.Option(None, "--importance", "-i", help="0..1 (padrão: inferido)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Escreve um registro de memória (normalmente feito pelo Runtime)."""

    if kind not in KINDS:
        error(f"tipo inválido: {kind} (válidos: {', '.join(KINDS)})")
        raise typer.Exit(code=1)
    runtime = get_runtime(workspace)
    record = runtime.memory.write(
        content,
        kind=MemoryKind(kind),
        namespace=namespace,
        tags=[item.strip() for item in tags.split(",") if item.strip()],
        source="cli",
        importance=importance,
    )
    success(f"memória registrada: {record.id} (importância {record.importance})")


@app.command(name="show")
def show(
    record_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Mostra um registro completo, com saliência e estado de vida."""

    runtime = get_runtime(workspace)
    record = runtime.memory.get(record_id)
    if record is None:
        error(f"memória {record_id} não encontrada")
        raise typer.Exit(code=1)
    if as_json:
        json_output(record.model_dump(mode="json"))
        return
    salience_value = _salience_of(runtime, record)
    kv(
        f"Memória {record.id}",
        {
            "namespace": record.namespace,
            "tipo": str(record.kind),
            "origem": record.source,
            "importância": record.importance,
            "acessos": record.access_count,
            "idade (dias)": round(record.age_days, 1),
            "saliência": salience_value,
            "estado": _classify(runtime, salience_value),
            "arquivada": record.archived,
            "duplicata de": record.duplicate_of or "-",
            "vetor": record.embedding_model or "ausente",
            "tags": ", ".join(record.tags) or "-",
            "task": record.task_id or "-",
        },
    )
    info(record.content[:600])


@app.command(name="list")
def list_memory(
    namespace: str = typer.Option(None, "--namespace", "-n"),
    kind: str = typer.Option(None, "--kind", "-k"),
    limit: int = typer.Option(20, "--limit", "-l"),
    archived: bool = typer.Option(False, "--archived"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista registros recentes."""

    runtime = get_runtime(workspace)
    records = runtime.memory.list(namespace=namespace, limit=limit, include_archived=archived)
    if kind:
        records = [record for record in records if str(record.kind) == kind]
    if as_json:
        json_output([record.model_dump(mode="json") for record in records])
        return
    if not records:
        info("memória vazia")
        return
    table(
        "Memória recente",
        ["id", "namespace", "tipo", "estado", "acessos", "resumo", "criado em"],
        [
            [
                record.id,
                record.namespace,
                record.kind,
                _classify(runtime, _salience_of(runtime, record)),
                record.access_count,
                (record.summary or record.content)[:50],
                record.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
            for record in records
        ],
    )


@app.command(name="consolidate")
def consolidate(
    apply: bool = typer.Option(False, "--apply", help="Executa (sem isso é só relatório)"),
    prune: bool = typer.Option(False, "--prune", help="Remover arquivados vencidos"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Near-duplicatas são arquivadas; a mais saliente sobrevive."""

    runtime = get_runtime(workspace)
    report = runtime.memory.consolidate(apply=apply, prune=prune)
    if as_json:
        json_output(report)
        return
    kv(
        "Consolidação",
        {
            "registros varridos": report["scanned"],
            "duplicatas detectadas": report["duplicates"],
            "arquivadas": report["archived"],
            "podáveis (retenção vencida)": report["prunable"],
            "removidas": report["pruned"],
            "aplicado": report["applied"],
        },
    )
    if report["pairs"]:
        table(
            "Pares (vencedor × arquivado)",
            ["vencedor", "arquivado", "cosseno", "saliência vencedora", "saliência arquivada"],
            [
                [pair["winner"], pair["loser"], pair["cosine"], pair["winner_salience"], pair["loser_salience"]]
                for pair in report["pairs"]
            ],
        )
    if not apply:
        warning("nada foi alterado: rode com --apply para arquivar")


@app.command(name="reindex")
def reindex(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Reconstrói os vetores semânticos (troca de modelo ou acervo antigo)."""

    runtime = get_runtime(workspace)
    report = runtime.memory.reindex()
    if as_json:
        json_output(report)
        return
    kv(
        "Reindexação",
        {
            "registros": report["total"],
            "vetores reconstruídos": report["rebuilt"],
            "modelo": report["model"],
            "dimensão": report["dimension"],
        },
    )
    success("vetores atualizados")


@app.command(name="forget")
def forget(
    record_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Remove uma memória de verdade (registro + índice + vetor)."""

    runtime = get_runtime(workspace)
    if not runtime.memory.forget(record_id):
        error(f"memória {record_id} não encontrada")
        raise typer.Exit(code=1)
    success(f"memória {record_id} esquecida")


@app.command(name="stats")
def stats(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Estatísticas: tipos, namespaces, vetores e distribuição de saliência."""

    runtime = get_runtime(workspace)
    data = runtime.memory.stats()
    if as_json:
        json_output(data)
        return
    kv(
        "Memória",
        {
            "total": data["total"],
            "ativas": data["active"],
            "arquivadas": data["archived"],
            "saliência média": data["avg_salience"],
            "modelo de embedding": f"{data['model']} ({data['dimension']} dims)",
            "recuperação": data["retrieval"],
            "vetores": data["vectors"],
            "sem vetor": data["without_vector"],
        },
    )
    if data["by_kind"]:
        table("Tipos", ["tipo", "total"], [[kind, total] for kind, total in data["by_kind"].items()])
    if data["by_namespace"]:
        table(
            "Namespaces",
            ["namespace", "total"],
            [[name, total] for name, total in data["by_namespace"].items()],
        )
    table(
        "Ciclo de vida",
        ["estado", "registros"],
        [[state, total] for state, total in data["distribution"].items()],
    )


def _salience_of(runtime, record) -> float:
    from ...memory.salience import salience

    return salience(record, half_life_days=runtime.settings.config.memory.half_life_days)


def _classify(runtime, value: float) -> str:
    from ...memory.salience import classify

    return classify(value)


__all__ = ["app"]
