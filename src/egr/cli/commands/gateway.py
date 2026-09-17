"""Gateway: o Runtime conversando com gente (Fase 10).

    egr gateway status              # canais, pareamentos e mensagens
    egr gateway pair telegram 12345 --code ABC123 --role operator
    egr gateway console             # conversa pelo terminal
    egr gateway start --channel telegram
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table, warning

app = typer.Typer(no_args_is_help=True, help="Canais: Telegram, Slack, Web e terminal atrás do mesmo Runtime")


@app.command(name="status")
def status(
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Onde o Runtime conversa, com quem e desde quando."""

    runtime = get_runtime(workspace)
    data = runtime.channel_status()
    if as_json:
        json_output(data)
        return
    kv(
        "Gateway",
        {
            "habilitado": data["habilitado"],
            "pareamento exigido": data["pareamento_exigido"],
            "papéis padrão": ", ".join(data["papéis_padrão"]),
            "ritmo": f"{data['ritmo_por_minuto']}/min",
            "redação": data["redação"],
            "pareamentos": data["pareamentos"]["total"],
            "mensagens": data["mensagens"]["total"],
        },
    )
    if data["canais"]:
        table(
            "Canais",
            ["nome", "tipo", "habilitado", "agente", "ambiente", "lista branca", "decisões", "pareados"],
            [
                [
                    item["nome"],
                    item["tipo"],
                    "sim" if item["habilitado"] else "não",
                    item["agente"],
                    item["ambiente"],
                    item["lista_branca"],
                    "sim" if item["decisões"] else "não",
                    item["pareados"],
                ]
                for item in data["canais"]
            ],
        )
    else:
        info("nenhum canal declarado em egr.yaml (gateway.channels)")


@app.command(name="channels")
def channels(workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Lista os canais configurados (e se estão prontos para falar)."""

    runtime = get_runtime(workspace)
    data = runtime.channel_status()["canais"]
    if not data:
        info("nenhum canal declarado")
        return
    table(
        "Canais",
        ["nome", "tipo", "habilitado", "instanciado", "agente", "pareados"],
        [
            [
                item["nome"],
                item["tipo"],
                "sim" if item["habilitado"] else "não",
                "sim" if item["instanciado"] else "não",
                item["agente"],
                item["pareados"],
            ]
            for item in data
        ],
    )


@app.command(name="bindings")
def bindings(
    status: str = typer.Option(None, "--status", "-s", help="pending | active | blocked"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Quem fala com o Runtime por um canal, e com quais papéis."""

    runtime = get_runtime(workspace)
    rows = runtime.channels.list_bindings(status=status, limit=100)
    if as_json:
        json_output([binding.summary() for binding in rows])
        return
    if not rows:
        info("nenhum pareamento — envie uma mensagem ao canal para aparecer aqui")
        return
    table(
        "Pareamentos",
        ["id", "status", "principal", "papéis", "código", "pareado por", "mensagens"],
        [
            [
                binding.id,
                str(binding.status),
                binding.principal_id or "-",
                ", ".join(sorted(binding.roles)),
                binding.pairing_code or "-",
                binding.paired_by or "-",
                binding.message_count,
            ]
            for binding in rows
        ],
    )


@app.command(name="pair")
def pair(
    channel: str = typer.Argument(..., help="nome do canal (como em egr.yaml)"),
    external_id: str = typer.Argument(..., help="chat_id (Telegram), user_id (Slack) ou sessão"),
    code: str = typer.Option(None, "--code", "-c", help="código mostrado ao remetente"),
    role: list[str] = typer.Option(None, "--role", "-r", help="papéis (padrão: viewer)"),
    name: str = typer.Option("", "--name", "-n"),
    by: str = typer.Option("human:cli", "--by", "-b"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Autoriza um remetente a falar com o Runtime (ato de operador)."""

    runtime = get_runtime(workspace)
    try:
        binding = runtime.channels.pair(
            channel,
            external_id,
            roles=list(role) if role else None,
            code=code,
            actor=by,
            display_name=name,
        )
    except ValueError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"{binding.id} pareado como {binding.principal_id} ({', '.join(sorted(binding.roles))})")


@app.command(name="unpair")
def unpair(
    channel: str = typer.Argument(...),
    external_id: str = typer.Argument(...),
    block: bool = typer.Option(False, "--block", help="bloqueia (não volta a ficar pendente)"),
    by: str = typer.Option("human:cli", "--by", "-b"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Remove o pareamento — o remetente volta a não ser ninguém."""

    runtime = get_runtime(workspace)
    try:
        binding = runtime.channels.unpair(channel, external_id, actor=by, block=block)
    except ValueError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    warning(f"{binding.id} agora está {binding.status}")


@app.command(name="send")
def send(
    channel: str = typer.Argument(..., help="canal registrado (web, console, telegram, slack)"),
    external_id: str = typer.Argument(..., help="remetente/destino"),
    text: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Manda uma mensagem como se viesse do canal (testa permissão de verdade)."""

    runtime = get_runtime(workspace)
    reply = runtime.channels.handle(channel, external_id, text)
    if as_json:
        json_output(reply.summary())
        return
    if reply.denied:
        warning(f"recusada ({reply.reason}): {reply.text}")
        raise typer.Exit(code=1)
    success(f"task {reply.task_id}" if reply.task_id else "respondido")
    for line in reply.text.splitlines():
        info(line)


@app.command(name="console")
def console(
    turns: int = typer.Option(0, "--turns", "-t", help="encerra depois de N mensagens (0 = livre)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Conversa pelo terminal — o canal mais honesto que existe."""

    from ...gateway.channels import ConsoleChannel

    runtime = get_runtime(workspace)
    channel = runtime.channels.channel("console")
    if channel is None:
        channel = ConsoleChannel("console", None)
        runtime.channels.register(channel)
    if not runtime.settings.config.gateway.enabled:
        warning("gateway desabilitado em egr.yaml: as mensagens serão recusadas")
    info("canal console · /ajuda lista os comandos · Ctrl+D sai")
    channel.run(runtime.channels.handle_inbound, max_turns=turns)


@app.command(name="start")
def start(
    channel: list[str] = typer.Option(None, "--channel", "-c", help="canal (padrão: todos)"),
    once: bool = typer.Option(False, "--once", help="uma rodada de polling e sai"),
    interval: float = typer.Option(1.0, "--interval", "-i", help="segundos entre rodadas"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Atende os canais de polling (Telegram) em primeiro plano."""

    from ...gateway.loop import run

    runtime = get_runtime(workspace)
    names = list(channel) if channel else None
    try:
        runner = run(runtime, names, once=once, interval=interval)
    except KeyError as exc:
        error(str(exc).strip('"'))
        raise typer.Exit(code=1) from exc
    if runner.errors:
        for item in runner.errors:
            warning(f"{item['canal']}: {item['erro']}")
    info(f"{runner.handled} mensagem(ns) tratada(s)")


@app.command(name="messages")
def messages(
    channel: str = typer.Option(None, "--channel", "-c"),
    limit: int = typer.Option(10, "--limit", "-l"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Histórico da conversa (texto já redigido e truncado)."""

    runtime = get_runtime(workspace)
    rows = runtime.gateway_messages.list(channel=channel, limit=limit)
    if as_json:
        json_output([item.summary() for item in rows])
        return
    if not rows:
        info("nenhuma mensagem registrada")
        return
    table(
        "Mensagens",
        ["quando", "canal", "direção", "remetente", "task", "texto"],
        [
            [
                item.created_at.strftime("%d/%m %H:%M:%S"),
                item.channel,
                "entrada" if item.direction == "in" else "saída",
                item.external_id,
                item.task_id or "-",
                item.text[:60],
            ]
            for item in rows
        ],
    )


@app.command(name="attachments")
def attachments(
    channel: str | None = typer.Option(None, "--channel", "-c", help="filtra por canal"),
    external_id: str | None = typer.Option(None, "--remetente", "-r"),
    status: str | None = typer.Option(None, "--status", "-s", help="received | stored | rejected"),
    limit: int = typer.Option(10, "--limit", "-l"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Arquivos que entraram pelos canais — aceitos e recusados."""

    runtime = get_runtime(workspace)
    rows = runtime.gateway_attachments.list(channel=channel, external_id=external_id, status=status, limit=limit)
    if as_json:
        json_output([item.summary() for item in rows])
        return
    if not rows:
        info("nenhum anexo registrado")
        return
    table(
        "Anexos",
        ["quando", "canal", "remetente", "nome", "tamanho", "situação", "caminho"],
        [
            [
                item.created_at.strftime("%d/%m %H:%M:%S"),
                item.channel,
                item.external_id,
                item.name[:28],
                f"{item.size} B",
                str(item.status),
                (item.path or item.reason)[:34],
            ]
            for item in rows
        ],
    )
    stats = runtime.gateway_attachments.stats()
    if stats:
        kv("Por situação", stats)


@app.command(name="attachment")
def attachment(
    attachment_id: str = typer.Argument(..., help="id do anexo"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Detalha um anexo: onde está, o que é e o que foi extraído."""

    runtime = get_runtime(workspace)
    item = runtime.gateway_attachments.get(attachment_id)
    if item is None:
        error(f"anexo não encontrado: {attachment_id}")
        raise typer.Exit(code=1)
    if as_json:
        json_output({**item.summary(), "trecho": item.preview})
        return
    kv(
        "Anexo",
        {
            "id": item.id,
            "nome": item.name,
            "tipo": item.mime or "-",
            "tamanho": f"{item.size} B",
            "caminho": item.path or "-",
            "impressão": item.checksum or "-",
            "situação": str(item.status),
            "motivo": item.reason or "-",
            "task": item.task_id or "-",
            "remetente": f"{item.channel}:{item.external_id}",
        },
    )
    if item.preview:
        info("trecho redigido:")
        for line in item.preview.splitlines()[:12]:
            info(f"  {line}")


@app.command(name="send-file")
def send_file(
    channel: str = typer.Argument(..., help="canal registrado"),
    external_id: str = typer.Argument(..., help="destino"),
    path: str = typer.Argument(..., help="arquivo do workspace (raízes liberadas na configuração)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Manda um arquivo do workspace pelo canal (saída conferida)."""

    runtime = get_runtime(workspace)
    prepared = runtime.channels.attachments.outbound(path)
    if not prepared.get("ok"):
        error(f"não enviado: {prepared.get('erro')}")
        raise typer.Exit(code=1)
    instance = runtime.channels.channel(channel)
    if instance is None:
        error(f"canal não registrado: {channel}")
        raise typer.Exit(code=1)
    result = instance.send_attachment(
        external_id,
        prepared["absolute"],
        name=prepared["name"],
        mime=prepared["mime"],
    )
    success(f"{prepared['name']} enviado para {channel}:{external_id} ({prepared['size']} B)")
    if isinstance(result, dict) and result.get("ok") is False:
        warning(str(result.get("error") or result.get("erro") or ""))


@app.command(name="interact")
def interact(
    channel: str = typer.Argument(..., help="canal registrado"),
    external_id: str = typer.Argument(..., help="remetente"),
    action: str = typer.Argument(..., help="aprovar | recusar | repetir | ajuda"),
    value: str = typer.Argument("", help="valor (id da aprovação, por exemplo)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Aperta um botão: a interação vira comando e passa pelo mesmo governo."""

    runtime = get_runtime(workspace)
    reply = runtime.channels.handle_interaction(channel, external_id, action, value)
    if reply.denied:
        warning(f"recusada ({reply.reason}): {reply.text}")
        raise typer.Exit(code=1)
    success(reply.text)


@app.command(name="upload")
def upload(
    channel: str = typer.Argument(..., help="canal registrado"),
    external_id: str = typer.Argument(..., help="remetente"),
    file: Path = typer.Argument(..., help="arquivo a entregar pela fronteira governada"),
    text: str = typer.Option("", "--text", "-t", help="mensagem que acompanha o arquivo"),
    mime: str = typer.Option("", "--mime", "-m", help="tipo declarado (quando o arquivo não diz)"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Entrega um arquivo ao Gateway (tipo e tamanho conferidos antes de entrar)."""

    from ...domain.channel import InboundAttachment, InboundMessage

    runtime = get_runtime(workspace)
    if not file.exists() or not file.is_file():
        error(f"arquivo não encontrado: {file}")
        raise typer.Exit(code=1)
    content = file.read_bytes()
    pending = InboundAttachment(
        name=file.name, mime=mime, size=len(content), content=content, remote_ref="cli"
    )
    if text.strip():
        reply = runtime.channels.handle_inbound(
            InboundMessage(
                channel=channel, external_id=external_id, text=text, attachments=[pending]
            )
        )
        if as_json:
            json_output(reply.summary())
            return
        if reply.denied:
            warning(f"recusada ({reply.reason}): {reply.text}")
            raise typer.Exit(code=1)
        success(f"task {reply.task_id}" if reply.task_id else "processado")
        for line in reply.text.splitlines():
            info(line)
        return

    stored = runtime.channels.attachments.receive(channel, external_id, pending)
    if as_json:
        json_output(stored.summary())
        return
    if not stored.stored:
        warning(f"recusado ({stored.reason}): {stored.name}")
        raise typer.Exit(code=1)
    kv(
        "Anexo",
        {
            "id": stored.id,
            "nome": stored.name,
            "tipo": stored.mime or "-",
            "tamanho": f"{stored.size} B",
            "caminho": stored.path,
            "impressão": stored.checksum[:16],
            "situação": str(stored.status),
        },
    )


__all__ = ["app"]
