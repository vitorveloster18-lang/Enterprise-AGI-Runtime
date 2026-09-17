"""Mudanças no ambiente de desenvolvimento, com prova e aprovação humana.

    propor → verificar → provar → aprovar → aplicar → promover

O `apply` é o único escritor: ele exige proposta aprovada e confere a impressão
digital do que foi aprovado antes de escrever. `dev.apply` não é ferramenta de
agente, portanto este módulo é o caminho oficial de mudança.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, kv, panel, success, table, warning

app = typer.Typer(no_args_is_help=True, help="Proposta de mudança controlada no ambiente de desenvolvimento")

KINDS = ["agent", "tool", "workflow", "policy"]


@app.command(name="create")
def create(
    kind: str = typer.Argument(..., help="agent | tool | workflow | policy"),
    name: str = typer.Argument(..., help="identificador do artefato"),
    content: Path = typer.Option(None, "--file", "-f", help="arquivo com o conteúdo proposto"),
    reason: str = typer.Option("", "--reason", "-r", help="por que esta mudança existe"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cria uma proposta de mudança no ambiente de desenvolvimento."""

    if kind not in KINDS:
        error(f"tipo inválido: {kind} (use {', '.join(KINDS)})")
        raise typer.Exit(code=2)
    if content is None:
        error("informe --file com o conteúdo proposto")
        raise typer.Exit(code=2)

    runtime = get_runtime(workspace)
    proposal = runtime.dev_propose(
        kind, name, content.read_text(encoding="utf-8"), reason or f"proposta via CLI ({content.name})"
    )
    success(f"proposta {proposal.id} criada ({proposal.kind}:{proposal.name})")
    kv(
        proposal.id,
        {
            "artefato": f"{proposal.kind}:{proposal.name}",
            "status": str(proposal.status),
            "ambiente": str(proposal.environment),
            "motivo": proposal.reason or "-",
        },
    )
    info(f"egr proposal verify {proposal.id}")


@app.command(name="verify")
def verify(proposal_id: str = typer.Argument(...), workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Roda as verificações estáticas da proposta (schema, segurança e alcance)."""

    runtime = get_runtime(workspace)
    proposal = runtime.dev_verify(proposal_id)
    kv(
        proposal.id,
        {
            "artefato": f"{proposal.kind}:{proposal.name}",
            "status": str(proposal.status),
            "checks": f"{len(proposal.checks) - len(proposal.errors)}/{len(proposal.checks)}",
        },
    )
    table(
        "Verificações",
        ["resultado", "verificação", "detalhe"],
        [
            ["ok" if check.ok else ("aviso" if check.level == "warning" else "FALHA"), check.name, check.detail[:100]]
            for check in proposal.checks
        ],
    )
    if proposal.errors:
        panel("Impedimentos", "\n".join(f"• {check.detail}" for check in proposal.errors), style="red")
    else:
        info(f"egr proposal prove {proposal.id}")


@app.command(name="prove")
def prove(
    proposal_id: str = typer.Argument(...),
    suite: str = typer.Option(None, "--suite", "-s", help="suíte de avaliação (padrão: smoke do tipo)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Roda a avaliação da proposta e anexa o resultado como evidência."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        proposal, run = runtime.dev_prove(proposal_id, suite_id=suite)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    kv(
        proposal.id,
        {
            "artefato": f"{proposal.kind}:{proposal.name}",
            "status": str(proposal.status),
            "evidência": run.id,
            "suíte": run.suite_id,
            "resultado": str(run.status),
            "taxa de acerto": f"{run.pass_rate:.0%} ({run.passed_cases}/{run.total_cases})",
        },
    )
    if proposal.status == "proved":
        success(f"proposta provada — egr proposal approve {proposal.id}")
    else:
        warning(f"avaliação terminou em '{run.status}': a proposta segue {proposal.status}")


@app.command(name="approve")
def approve(
    proposal_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b", help="quem aprova (identidade verificada quando exigida)"),
    token: str = typer.Option(None, "--token", help="token egr_<id>.<segredo>"),
    note: str = typer.Option("", "--note", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Aprova uma proposta verificada e provada (ato humano)."""

    from ...core.errors import AuthorizationError, ConfigError

    runtime = get_runtime(workspace)
    try:
        proposal = runtime.dev_approve(proposal_id, by, token=token, note=note)
    except (ConfigError, AuthorizationError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"{proposal.id} aprovada por {proposal.decided_by}")
    info(f"egr proposal apply {proposal.id}")


@app.command(name="reject")
def reject(
    proposal_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b"),
    note: str = typer.Option("", "--note", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Recusa uma proposta (ato humano)."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        proposal = runtime.dev_reject(proposal_id, by, note=note)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    warning(f"{proposal.id} recusada")


@app.command(name="apply")
def apply(
    proposal_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Único escritor: aplica uma proposta aprovada, conferindo a impressão digital."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        result = runtime.dev_apply(proposal_id, actor=by)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    success(f"{proposal_id} aplicada")
    kv(
        "Aplicação",
        {
            "artefato": f"{result['kind']}:{result['name']}",
            "destino": result.get("path", "-"),
            "status": result.get("status", "aplicada"),
            "impressão digital conferida": result.get("fingerprint", "-"),
            "recarregado": result.get("reloaded", True),
        },
    )
    info("avaliar antes de promover: egr eval run --suite <suíte> --target <artefato>")


@app.command(name="list")
def list_proposals(
    status: str = typer.Option(None, "--status", "-s"),
    limit: int = typer.Option(20, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Lista propostas de mudança."""

    runtime = get_runtime(workspace)
    proposals = runtime.dev_list(status=status, limit=limit)
    if not proposals:
        info("nenhuma proposta — `egr proposal create tool nome --file tools/nome.py`")
        return
    table(
        "Propostas",
        ["id", "artefato", "status", "ambiente", "motivo"],
        [
            [
                proposal.id,
                f"{proposal.kind}:{proposal.name}",
                str(proposal.status),
                str(proposal.environment),
                (proposal.reason or "-")[:60],
            ]
            for proposal in proposals
        ],
    )


@app.command(name="show")
def show(proposal_id: str = typer.Argument(...), workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Mostra uma proposta com todas as verificações."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    try:
        proposal = runtime.dev_show(proposal_id)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    kv(
        proposal.id,
        {
            "artefato": f"{proposal.kind}:{proposal.name}",
            "status": str(proposal.status),
            "ambiente": str(proposal.environment),
            "motivo": proposal.reason or "-",
            "criada em": proposal.created_at.isoformat(),
            "decidida por": proposal.decided_by or "-",
            "nota": proposal.decision_note or "-",
            "evidências": ", ".join(proposal.evidence_run_ids) or "-",
        },
    )
    if proposal.checks:
        table(
            "Verificações",
            ["resultado", "verificação", "detalhe"],
            [
                [
                    "ok" if check.ok else ("aviso" if check.level == "warning" else "FALHA"),
                    check.name,
                    check.detail[:100],
                ]
                for check in proposal.checks
            ],
        )


@app.command(name="deploy")
def deploy(
    proposal_id: str = typer.Argument(...),
    target: str = typer.Option("staging", "--to", "-t", help="staging | production"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cria um release para esta proposta já aprovada e aplicada (Fase 9)."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    proposal = runtime.dev_show(proposal_id)
    if str(proposal.status) != "applied":
        error(f"proposta {proposal_id} está {proposal.status}: aplique antes de promover")
        raise typer.Exit(code=1)
    if target not in ("staging", "production"):
        error(f"ambiente inválido: {target}")
        raise typer.Exit(code=2)
    try:
        release = runtime.release_manager.create(
            [(proposal.kind, proposal.name)],
            target=target,
            title=f"Promoção de {proposal.id}",
            reason=f"promoção de {proposal.kind}:{proposal.name} para {target}",
            created_by=proposal.decided_by or "cli",
        )
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"release {release.id} criado → {release.target}")
    info(f"egr release submit {release.id} && egr release approve {release.id}")


@app.command(name="rollback")
def rollback(
    proposal_id: str = typer.Argument(...),
    note: str = typer.Option("", "--note", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Reverte o último release que levou este artefato adiante (Fase 9)."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    proposal = runtime.dev_show(proposal_id)
    for release in runtime.release_manager.list(limit=100):
        if str(release.status) != "deployed":
            continue
        if any(item.kind == proposal.kind and item.name == proposal.name for item in release.items):
            try:
                undone = runtime.release_manager.rollback(release.id, note=note or f"reversão de {proposal_id}")
            except ConfigError as exc:
                error(str(exc))
                raise typer.Exit(code=1) from exc
            warning(f"{release.id} revertido por {undone.id}")
            return
    error(f"nenhum release aplicado envolve {proposal.kind}:{proposal.name}")
    raise typer.Exit(code=1)


__all__ = ["app"]
