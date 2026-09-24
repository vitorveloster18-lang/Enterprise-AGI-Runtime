"""Integrações: o Runtime conversando com sistemas (Fase 11).

    egr integration list                    # conectores declarados
    egr integration show CRM                # o que ele pode fazer
    egr integration enable CRM --by ops     # habilitar é um ato explícito
    egr integration test CRM                # teste sem efeito colateral
    egr integration call CRM /clientes      # chamada governada
    egr integration calls                   # o que saiu, quanto custou
    egr integration events                  # o que chegou de fora
    egr integration sync                    # integrations/*.yaml -> registro
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer

from ...domain.enums import EventType
from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table, warning

app = typer.Typer(no_args_is_help=True, help="Conectores: REST, GraphQL, SQL e webhooks atrás da mesma política")


@app.command(name="list")
def list_integrations(
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Conectores declarados e o que cada um pode fazer."""

    runtime = get_runtime(workspace)
    data = runtime.integrations_status()
    if as_json:
        json_output(data)
        return
    kv(
        "Integrações",
        {
            "habilitado": data["habilitado"],
            "redação": data["redação"],
            "drivers SQL": ", ".join(data["drivers_sql"]),
            "conectores": data["conectores"]["total"],
            "habilitados": data["conectores"]["habilitados"],
            "chamadas": data["chamadas"]["total"],
            "eventos": data["eventos"]["total"],
        },
    )
    itens = data["conectores"]["itens"]
    if not itens:
        info("nenhum conector — declare em integrations/*.yaml e rode `egr integration sync`")
        return
    table(
        "Conectores",
        ["id", "tipo", "habilitado", "destino", "hosts", "métodos", "auth", "entrada"],
        [
            [
                item["id"],
                item["tipo"],
                "sim" if item["habilitado"] else "não",
                item["destino"],
                item["hosts"],
                item["métodos"],
                item["autenticação"],
                "sim" if item["entrada"] else "não",
            ]
            for item in itens
        ],
    )


@app.command(name="show")
def show(
    integration: str = typer.Argument(..., help="id do conector"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """O que este conector pode fazer — e o que ele nunca vai fazer."""

    runtime = get_runtime(workspace)
    try:
        item = runtime.connectors.get(integration)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        json_output(item.model_dump(mode="json"))
        return
    kv(f"Conector — {item.id}", item.summary())
    if item.inbound.enabled:
        kv(
            "Entrada (webhook)",
            {
                "habilitado": item.inbound.enabled,
                "assinatura": item.inbound.signature_header,
                "id do evento": item.inbound.event_id_header,
                "tipo publicado": item.inbound.event_type or "-",
                "tolerância": f"{item.inbound.tolerance_seconds}s",
                "segredo": (item.inbound.secret.split(":", 1)[0] if item.inbound.secret else "-"),
            },
        )
    chamadas = runtime.integration_calls.list(integration=item.id, limit=5)
    if chamadas:
        table(
            "Últimas chamadas",
            ["id", "método", "ok", "status", "latência", "decisão", "quando"],
            [
                [
                    call.id,
                    call.method,
                    "sim" if call.ok else "não",
                    call.status,
                    f"{call.latency_ms} ms",
                    call.decision,
                    call.summary()["quando"],
                ]
                for call in chamadas
            ],
        )


@app.command(name="sync")
def sync(workspace: Path = typer.Option(None, "--workspace", "-w")):
    """integrations/*.yaml -> registro (declaração é a fonte da verdade)."""

    runtime = get_runtime(workspace)
    integracoes = runtime.sync_integrations()
    success(f"{len(integracoes)} conector(es) sincronizado(s)")
    for item in integracoes:
        info(f"{item.id} ({item.kind}) — {'habilitado' if item.enabled else 'desabilitado'}")


@app.command(name="enable")
def enable(
    integration: str = typer.Argument(...),
    disable: bool = typer.Option(False, "--disable", help="desabilita em vez de habilitar"),
    by: str = typer.Option("human:cli", "--by", "-b"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Habilitar um conector é um ato administrativo — e fica na trilha."""

    runtime = get_runtime(workspace)
    try:
        item = runtime.connectors.get(integration)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    item.enabled = not disable
    runtime.connectors.register(item)
    runtime.audit.record(
        EventType.SYSTEM_EVENT,
        actor=by,
        payload={"action": "disable" if disable else "enable", "conector": item.id},
    )
    success(f"{item.id} {'desabilitado' if disable else 'habilitado'}")


@app.command(name="test")
def test(
    integration: str = typer.Argument(..., help="id do conector"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Teste sem efeito colateral (`GET /`, `select 1` ou introspecção)."""

    runtime = get_runtime(workspace)
    try:
        call = runtime.connectors.test(integration)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        json_output(call.summary())
        return
    if call.ok:
        success(f"{call.integration} respondeu em {call.latency_ms} ms (status {call.status})")
    else:
        warning(f"{call.integration} não respondeu: {call.error}")
        raise typer.Exit(code=1)


@app.command(name="call")
def call(
    integration: str = typer.Argument(..., help="id do conector"),
    path: str = typer.Argument("", help="caminho (REST/GraphQL)"),
    method: str = typer.Option("GET", "--method", "-X"),
    query: str = typer.Option("", "--query", "-q", help="query GraphQL ou SQL"),
    body: str = typer.Option("", "--data", "-d", help="corpo da requisição"),
    var: list[str] = typer.Option([], "--var", help="variáveis no formato chave=valor"),
    dry_run: bool = typer.Option(False, "--dry-run", help="avalia sem executar"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Chama um conector declarado: política decide, trilha registra."""

    from ..formatting import parse_kv

    runtime = get_runtime(workspace)
    variables = parse_kv(var)
    try:
        chamada = runtime.connectors.call(
            integration,
            method=method,
            path=path,
            query=query,
            body=body or None,
            variables=variables,
            dry_run=dry_run,
            actor="human:cli",
        )
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        json_output(chamada.summary())
        return
    if not chamada.ok:
        warning(f"recusada: {chamada.error}")
        raise typer.Exit(code=1)
    success(f"{chamada.method} {chamada.target} → ok em {chamada.latency_ms} ms")
    kv(
        "Chamada",
        {
            "id": chamada.id,
            "decisão": chamada.decision,
            "status": chamada.status,
            "custo": chamada.cost,
            "resposta": chamada.response_summary or "-",
        },
    )


@app.command(name="calls")
def calls(
    integration: str = typer.Option(None, "--integration", "-i"),
    limit: int = typer.Option(20, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """O que saiu daqui: destino, decisão, latência, custo e ator."""

    runtime = get_runtime(workspace)
    rows = runtime.integration_calls.list(integration=integration, limit=limit)
    if as_json:
        json_output([call.summary() for call in rows])
        return
    if not rows:
        info("nenhuma chamada registrada")
        return
    table(
        "Chamadas",
        ["id", "conector", "método", "destino", "ok", "latência", "custo", "decisão", "ator", "quando"],
        [
            [
                call.id,
                call.integration,
                call.method,
                call.target,
                "sim" if call.ok else "não",
                f"{call.latency_ms} ms",
                call.cost,
                call.decision,
                call.actor,
                call.summary()["quando"],
            ]
            for call in rows
        ],
    )
    stats = runtime.integration_calls.stats()
    if stats:
        table(
            "Por conector",
            ["conector", "total", "sucesso", "custo"],
            [[nome, dado["total"], dado["sucesso"], dado["custo"]] for nome, dado in stats.items()],
        )


@app.command(name="enqueue")
def enqueue(
    integration: str = typer.Argument(..., help="id do conector"),
    path: str = typer.Argument("", help="caminho (REST/GraphQL)"),
    method: str = typer.Option("GET", "--method", "-X"),
    query: str = typer.Option("", "--query", "-q", help="query GraphQL ou SQL"),
    body: str = typer.Option("", "--data", "-d"),
    key: str = typer.Option(None, "--key", "-k", help="chave de idempotência"),
    attempts: int = typer.Option(None, "--attempts", "-t", help="tentativas antes de desistir"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Promete uma chamada: entra na fila, não tenta agora."""

    runtime = get_runtime(workspace)
    try:
        job = runtime.connectors.enqueue(
            integration,
            method=method,
            path=path,
            query=query,
            body=body or None,
            idempotency=key,
            max_attempts=attempts,
            actor="human:cli",
        )
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        json_output(job.summary())
        return
    success(f"job {job.id} na fila ({job.method} {job.integration})")
    info("processar: egr integration drain")


@app.command(name="jobs")
def jobs(
    status: str = typer.Option(None, "--status", "-s", help="pending | running | done | failed | cancelled"),
    integration: str = typer.Option(None, "--integration", "-i"),
    limit: int = typer.Option(20, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """A fila de saída: o que está prometido, o que falhou e por quê."""

    runtime = get_runtime(workspace)
    rows = runtime.integration_jobs.list(status=status, integration=integration, limit=limit)
    if as_json:
        json_output([job.summary() for job in rows])
        return
    if not rows:
        info("fila vazia")
        return
    table(
        "Fila de saída",
        ["id", "conector", "método", "status", "tentativas", "próxima", "erro", "quando"],
        [
            [
                job.id,
                job.integration,
                job.method,
                str(job.status),
                f"{job.attempts}/{job.max_attempts}",
                job.summary()["próxima"],
                (job.last_error or "-")[:40],
                job.summary()["quando"],
            ]
            for job in rows
        ],
    )
    stats = runtime.integration_jobs.stats()
    if stats:
        kv("Por status", stats)


@app.command(name="drain")
def drain(
    limit: int = typer.Option(None, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Processa os jobs cuja espera venceu (com espera crescente entre tentativas)."""

    runtime = get_runtime(workspace)
    resultados = runtime.connectors.drain(limit)
    if as_json:
        json_output(resultados)
        return
    if not resultados:
        info("nada pendente")
        return
    table(
        "Processados",
        ["id", "conector", "status", "tentativas", "próxima", "erro"],
        [
            [
                item["id"],
                item["conector"],
                item["status"],
                item["tentativas"],
                item["próxima"],
                (item["erro"] or "-")[:40],
            ]
            for item in resultados
        ],
    )


@app.command(name="cancel")
def cancel(
    job: str = typer.Argument(..., help="id do job"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cancela um job que ainda não foi executado."""

    runtime = get_runtime(workspace)
    try:
        cancelado = runtime.connectors.cancel(job, actor="human:cli")
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    warning(f"{cancelado.id} cancelado")


@app.command(name="events")
def events(
    integration: str = typer.Option(None, "--integration", "-i"),
    limit: int = typer.Option(20, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """O que chegou de fora — e o que o Runtime fez com isso."""

    runtime = get_runtime(workspace)
    rows = runtime.integration_events.list(integration=integration, limit=limit)
    if as_json:
        json_output([event.summary() for event in rows])
        return
    if not rows:
        info("nenhum evento recebido")
        return
    table(
        "Eventos de entrada",
        ["id", "conector", "id externo", "evento", "status", "assinatura", "quando"],
        [
            [
                event.id,
                event.integration,
                event.external_id or "-",
                event.event_type or "-",
                str(event.status),
                "ok" if event.signature_ok else "—",
                event.summary()["quando"],
            ]
            for event in rows
        ],
    )


@app.command(name="worker")
def worker(
    interval: float = typer.Option(30.0, "--interval", "-i", help="segundos entre rodadas"),
    rounds: int = typer.Option(0, "--rounds", "-n", help="encerra depois de N rodadas (0 = sem fim)"),
    batch: int = typer.Option(0, "--batch", help="jobs por rodada (0 = da configuração)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Fica olhando a fila e drena o que já venceu (Ctrl+C encerra)."""

    import signal

    from ...integrations.worker import QueueWorker

    runtime = get_runtime(workspace)
    if not runtime.settings.config.integrations.queue.enabled:
        warning("fila desabilitada em egr.yaml (integrations.queue.enabled)")
        raise typer.Exit(code=1)

    instance = QueueWorker(
        runtime,
        interval=interval,
        batch=batch or None,
        logger=lambda message: info(message),
    )

    def stop() -> bool:
        return instance.stopped

    def finish(signum, frame):  # encerra no fim da rodada, não no meio do job
        instance.stop()

    for signal_name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(AttributeError, ValueError):
            signal.signal(getattr(signal, signal_name), finish)

    info(f"drenando a cada {interval:.0f}s (Ctrl+C encerra)")
    summary = instance.run(max_rounds=rounds, stop=stop)
    kv(
        "Fila",
        {
            "rodadas": summary["rodadas"],
            "processados": summary["processados"],
            "intervalo": f"{summary['intervalo']:.0f}s",
            "encerrado": "sim" if summary["parado"] else "não",
        },
    )


__all__ = ["app"]
