"""Release: promoção entre ambientes (Fase 9).

    criar (snapshot + gates) → submeter → aprovar → aplicar → (reverter)

`egr release` é o caminho completo; `egr proposal deploy|rollback` continuam
existindo como atalhos do ciclo dev → staging → produção.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, json_output, kv, panel, success, table, warning

app = typer.Typer(no_args_is_help=True, help="Release: dev → staging → produção com versão, evidência e volta")

KINDS = ["agent", "tool", "workflow", "policy"]


def _items(raw: list[str]) -> list[tuple[str, str]]:
    """Aceita `agent:nome` (ou `nome`, assumindo agente) e devolve pares."""

    parsed: list[tuple[str, str]] = []
    for token in raw:
        if ":" in token:
            kind, _, name = token.partition(":")
        else:
            kind, name = "agent", token
        kind = kind.strip().lower()
        if kind not in KINDS:
            error(f"tipo inválido em '{token}': {kind} (use {', '.join(KINDS)})")
            raise typer.Exit(code=2)
        parsed.append((kind, name.strip()))
    return parsed


def _show(release) -> None:
    summary = release.summary()
    kv(
        f"Release {release.id}",
        {
            "título": release.title or "-",
            "destino": summary["target"],
            "status": str(release.status),
            "itens": ", ".join(summary["versões"]) or "-",
            "gates": summary["gates"],
            "evidência": ", ".join(release.evidence) or "-",
            "motivo": release.reason or "-",
            "criado por": release.created_by,
            "decidido por": release.decided_by or "-",
            "nota": release.decision_note or "-",
            "aplicado em": release.deployed_at.isoformat() if release.deployed_at else "-",
            "reverte": release.rollback_of or "-",
        },
    )
    if release.checks:
        table(
            "Gates",
            ["resultado", "gate", "detalhe"],
            [
                [
                    "ok" if check.ok else ("aviso" if check.level == "warning" else "FALHA"),
                    check.name,
                    check.detail[:100],
                ]
                for check in release.checks
                if not check.ok or check.level != "info"
            ]
            or [["ok", "tudo", "nenhum impedimento"]],
        )
    if release.errors:
        panel(
            "Impedimentos",
            "\n".join(f"• {check.detail}" for check in release.errors),
            style="red",
        )


@app.command(name="status")
def status(
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Onde cada artefato está na escada de ambientes."""

    runtime = get_runtime(workspace)
    data = runtime.governance_status()
    if as_json:
        json_output(data)
        return
    kv(
        "Governança",
        {
            "escada": " → ".join(data["ladder"]),
            "releases": data["releases"]["total"],
            "por status": ", ".join(f"{k}={v}" for k, v in sorted(data["releases"]["by_status"].items())) or "-",
            "versões": data["versions"]["total"],
            "artefatos versionados": ", ".join(data["versions"]["artifacts"]) or "-",
            "identidade exigida": data["governance"]["identity_required"],
            "papel mínimo (produção)": data["governance"]["approval_min_role"],
        },
    )
    for environment, artifacts in data["deployed"].items():
        if artifacts:
            info(f"em {environment}: {', '.join(artifacts)}")


@app.command(name="create")
def create(
    items: list[str] = typer.Argument(..., help="artefatos: agent:nome, tool:nome, workflow:nome, policy:nome"),
    target: str = typer.Option("staging", "--to", "-t", help="staging | production"),
    title: str = typer.Option("", "--title"),
    reason: str = typer.Option("", "--reason", "-r"),
    by: str = typer.Option("cli", "--by", "-b"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cria um release: tira snapshot dos itens e confere os gates."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        release = runtime.release_manager.create(
            _items(items), target=target, title=title, reason=reason, created_by=by
        )
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    success(f"release {release.id} criado → {release.target}")
    _show(release)
    if release.errors:
        warning("gates reprovaram: nada será promovido até os impedimentos serem resolvidos")
        raise typer.Exit(code=1)


@app.command(name="check")
def check(release_id: str = typer.Argument(...), workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Reconfere os gates de um release."""

    runtime = get_runtime(workspace)
    _show(runtime.release_manager.check(release_id))


@app.command(name="submit")
def submit(release_id: str = typer.Argument(...), workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Submete à aprovação humana (falha se os gates reprovarem)."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        release = runtime.release_manager.submit(release_id)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"{release.id} submetido — agora: egr release approve {release.id}")


@app.command(name="approve")
def approve(
    release_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b", help="principal que aprova (use um id de identidade)"),
    token: str = typer.Option(None, "--token", help="token egr_<id>.<segredo> (identidade verificada)"),
    note: str = typer.Option("", "--note", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Vota a aprovação de um release (quórum pode exigir mais de um voto)."""

    from ...core.errors import AuthenticationError, AuthorizationError, ConfigError

    runtime = get_runtime(workspace)
    try:
        release = runtime.release_manager.approve(release_id, by, token=token, note=note)
        state = runtime.release_manager.quorum(release)
    except (ConfigError, AuthorizationError, AuthenticationError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    if str(release.status) == "submitted":
        warning(
            f"{release.id} com {state['obtido']} de {state['exigido']} votos — "
            f"faltam {state['exigido'] - state['obtido']}"
        )
        info(f"aprovadores: {', '.join(state['aprovadores']) or '-'}")
        return
    success(f"{release.id} aprovado por {release.decided_by}")


@app.command(name="sign")
def sign(
    release_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b", help="quem assina"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Assina o manifesto do release: aprova o conteúdo, não a intenção."""

    from ...core.errors import ConfigError, KeyStoreError

    runtime = get_runtime(workspace)
    try:
        release = runtime.release_manager.sign(release_id, actor=by)
    except (ConfigError, KeyStoreError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    signature = release.signature
    success(f"{release.id} assinado · {signature.algorithm} · chave {signature.key_id[:12]}")
    kv(f"Assinatura · {release.id}", {"impressão": signature.manifest_hash, "por": signature.signed_by})


@app.command(name="verify")
def verify(
    release_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Confere a assinatura: o release aplicado é o que foi aprovado."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        result = runtime.release_manager.verify(release_id)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    if result["válida"]:
        success(f"{release_id} assinatura válida")
        info(result["detalhe"])
    else:
        error(f"{release_id} assinatura inválida: {result['detalhe']}")
        raise typer.Exit(code=1)


@app.command(name="approvals")
def approvals(
    release_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Votos do release: quem aprovou, quem vetou e quanto falta do quórum."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        release = runtime.release_manager.get(release_id)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    state = runtime.release_manager.quorum(release)
    kv(
        f"Quórum · {release.id} → {state['destino']}",
        {
            "exigido": state["exigido"],
            "obtido": state["obtido"],
            "aprovadores": ", ".join(state["aprovadores"]) or "-",
            "vetos": ", ".join(state["vetos"]) or "-",
            "completo": "sim" if state["completo"] else "não",
        },
    )
    if not release.approvals:
        info("nenhum voto registrado")
        return
    table(
        "Votos",
        ["ator", "decisão", "papéis", "nota"],
        [
            [item.actor, item.decision, ", ".join(item.roles) or "-", item.note or "-"]
            for item in release.approvals
        ],
    )


@app.command(name="reject")
def reject(
    release_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b"),
    note: str = typer.Option("", "--note", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Recusa um release."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        release = runtime.release_manager.reject(release_id, by, note=note)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    warning(f"{release.id} recusado")


@app.command(name="deploy")
def deploy(
    release_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Aplica um release aprovado no ambiente de destino."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        release = runtime.release_manager.deploy(release_id, actor=by)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"{release.id} aplicado em {release.target}")
    _show(release)


@app.command(name="rollback")
def rollback(
    release_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b"),
    note: str = typer.Option("", "--note", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Volta cada item do release para a revisão anterior."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        release = runtime.release_manager.rollback(release_id, actor=by, note=note)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    warning(f"{release.rollback_of} revertido por {release.id}")
    _show(release)


@app.command(name="list")
def list_releases(
    status: str = typer.Option(None, "--status", "-s"),
    limit: int = typer.Option(20, "--limit", "-l"),
    as_json: bool = typer.Option(False, "--json", help="saída JSON (para consumo por automação)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Histórico de promoções."""

    runtime = get_runtime(workspace)
    releases = runtime.release_manager.list(status=status, limit=limit)
    if as_json:
        json_output([release.summary() for release in releases])
        return
    if not releases:
        info("nenhum release — `egr release create agent:nome --to staging`")
        return
    table(
        "Releases",
        ["id", "destino", "status", "itens", "gates", "criado por", "decidido por"],
        [
            [
                release.id,
                str(release.target),
                str(release.status),
                ", ".join(item.key for item in release.items),
                f"{len(release.checks) - len(release.errors)}/{len(release.checks)}",
                release.created_by,
                release.decided_by or "-",
            ]
            for release in releases
        ],
    )


@app.command(name="show")
def show(
    release_id: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Mostra um release: gates, evidência e itens."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        release = runtime.release_manager.get(release_id)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        json_output(release.model_dump(mode="json"))
        return
    _show(release)


@app.command(name="versions")
def versions(
    kind: str = typer.Argument(..., help="agent | tool | workflow | policy"),
    name: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json", help="saída JSON"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Versões (snapshots) de um artefato — o que o rollback pode restaurar."""

    runtime = get_runtime(workspace)
    rows = runtime.release_manager.versions.versions(kind, name)
    if as_json:
        json_output([version.model_dump(mode="json") for version in rows])
        return
    if not rows:
        info(f"nenhuma versão de {kind}:{name} (crie um release para versionar)")
        return
    table(
        f"Versões de {kind}:{name}",
        ["revisão", "versão", "ambiente", "impressão digital", "criado por", "release"],
        [
            [
                f"r{version.revision}",
                version.version or "-",
                str(version.environment),
                version.fingerprint,
                version.created_by,
                version.release_id or "-",
            ]
            for version in rows
        ],
    )


__all__ = ["app"]
