"""Development Environment: agentes e humanos construindo sob proposta.

    propor → verificar → provar → aprovar → aplicar

O agente chega até `propose`. O resto é do Runtime e do humano.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, kv, panel, success, table, warning

app = typer.Typer(no_args_is_help=True, help="Development Environment: criar agents/tools/workflows sob proposta")

KINDS = ["agent", "tool", "workflow", "policy"]


def _kind(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in KINDS:
        error(f"tipo inválido: {value} (use {', '.join(KINDS)})")
        raise typer.Exit(code=2)
    return value


@app.command(name="status")
def status_cmd(workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Estado do ambiente de desenvolvimento (propostas e ferramentas)."""

    runtime = get_runtime(workspace)
    data = runtime.dev_status()
    proposals = data["proposals"]
    kv(
        "Development environment",
        {
            "propostas": proposals["total"],
            "aguardando aprovação": proposals["awaiting_approval"],
            "por status": ", ".join(f"{k}={v}" for k, v in sorted(proposals["by_status"].items())) or "-",
            "por tipo": ", ".join(f"{k}={v}" for k, v in sorted(proposals["by_kind"].items())) or "-",
            "ferramentas do workspace": ", ".join(data["workspace_tools"]["files"]) or "-",
            "recusadas no carregamento": len(data["workspace_tools"].get("rejected") or []),
            "sandbox": f"{data['sandbox']['mode']} (configurado: {data['sandbox']['configured']})",
            "agentes": data["agents"],
            "workflows": data["workflows"],
        },
    )
    info(f"propostas e histórico em {data['dev_dir']}")


@app.command(name="new")
def new(
    kind: str = typer.Argument(..., help="agent | tool | workflow | policy"),
    name: str = typer.Argument(..., help="id do artefato (ex.: exemplo.linhas)"),
    from_file: Path = typer.Option(None, "--from", "-f", help="usa o conteúdo deste arquivo em vez do rascunho"),
    rationale: str = typer.Option("", "--rationale", "-r"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cria uma proposta (rascunho) — não escreve nada no workspace."""

    runtime = get_runtime(workspace)
    kind = _kind(kind)
    content = from_file.read_text(encoding="utf-8") if from_file else runtime.workbench.scaffold(kind, name)
    try:
        proposal = runtime.workbench.propose(kind, name, content, origin="human:cli", rationale=rationale)
    except Exception as exc:  # ConfigError e companhia
        error(f"{type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc

    success(f"proposta {proposal.id} criada → {proposal.target}")
    _show_checks(proposal)


@app.command(name="list")
def list_cmd(
    status: str = typer.Option(None, "--status", "-s", help="draft|validated|tested|approved|applied|rejected|failed"),
    kind: str = typer.Option(None, "--kind", "-k"),
    limit: int = typer.Option(20, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Lista propostas de mudança."""

    runtime = get_runtime(workspace)
    proposals = runtime.workbench.list(status=status, kind=kind, limit=limit)
    if not proposals:
        info("nenhuma proposta")
        return
    table(
        "Propostas",
        ["id", "tipo", "nome", "status", "origem", "risco", "verificações", "prova"],
        [
            [
                proposal.id,
                str(proposal.kind),
                proposal.name,
                str(proposal.status),
                proposal.origin,
                str(proposal.risk),
                f"{len(proposal.checks) - len(proposal.errors)}/{len(proposal.checks)}",
                ("ok" if proposal.tested_ok else "falhou") if proposal.trials else "-",
            ]
            for proposal in proposals
        ],
    )


@app.command(name="show")
def show(
    proposal_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Mostra uma proposta: verificações, provas e conteúdo."""

    runtime = get_runtime(workspace)
    proposal = _get(runtime, proposal_id)
    kv(
        f"Proposta {proposal.id}",
        {
            "tipo": str(proposal.kind),
            "nome": proposal.name,
            "destino": proposal.target,
            "status": str(proposal.status),
            "origem": proposal.origin,
            "risco": str(proposal.risk),
            "ambiente": str(proposal.environment),
            "justificativa": proposal.rationale or "-",
            "decidido por": proposal.decided_by or "-",
            "nota": proposal.decision_note or "-",
            "aplicada em": proposal.applied_at.isoformat() if proposal.applied_at else "-",
            "erro": proposal.error or "-",
        },
    )
    _show_checks(proposal)
    if proposal.trials:
        trial = proposal.last_trial
        panel(
            "Prova em sandbox",
            f"ok: {trial.ok}\nmodo: {trial.mode}\nduração: {trial.duration_ms} ms\n"
            f"saída: {str(trial.output)[:300]}\nerro: {trial.error or '-'}\n"
            f"arquivos: {', '.join(trial.files[:8]) or '-'}",
            style="green" if trial.ok else "red",
        )
    panel("Conteúdo", proposal.content[:2000], style="cyan")


@app.command(name="validate")
def validate(
    proposal_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Roda a verificação estática (nada é executado)."""

    runtime = get_runtime(workspace)
    proposal = runtime.workbench.validate(proposal_id)
    success(f"{proposal.id} → {proposal.status}")
    _show_checks(proposal)


@app.command(name="test")
def test(
    proposal_id: str = typer.Argument(...),
    args: str = typer.Option("{}", "--args", "-a", help="argumentos em JSON"),
    timeout: int = typer.Option(10, "--timeout", "-t"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Executa a ferramenta proposta dentro do sandbox (isolada, dry-run)."""

    runtime = get_runtime(workspace)
    try:
        payload = json.loads(args)
        if not isinstance(payload, dict):
            raise ValueError("esperado um objeto JSON")
    except ValueError as exc:
        error(f"--args inválido: {exc}")
        raise typer.Exit(code=2) from exc

    proposal = runtime.workbench.trial(proposal_id, payload, timeout=timeout)
    trial = proposal.last_trial
    if trial is None:  # pragma: no cover - defensivo
        error("nenhuma prova registrada")
        raise typer.Exit(code=1)
    panel(
        f"Prova {proposal.id}",
        f"status: {proposal.status}\nok: {trial.ok}\nmodo: {trial.mode}\n"
        f"duração: {trial.duration_ms} ms\nestourou o tempo: {trial.timed_out}\n"
        f"arquivos criados: {', '.join(trial.files[:8]) or '-'}\n"
        f"saída: {str(trial.output)[:500]}\nerro: {trial.error or '-'}",
        style="green" if trial.ok else "red",
    )


@app.command(name="diff")
def diff(
    proposal_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """O que muda no workspace se a proposta for aplicada."""

    runtime = get_runtime(workspace)
    text = runtime.workbench.diff(proposal_id)
    panel("Diff", text or "(arquivo novo — nada a comparar)", style="cyan")


@app.command(name="approve")
def approve(
    proposal_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b"),
    note: str = typer.Option("", "--note", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Aprova uma proposta verificada (ato humano)."""

    runtime = get_runtime(workspace)
    proposal = _approve_or_reject(runtime, proposal_id, approved=True, actor=by, note=note)
    success(f"{proposal.id} aprovada — agora: egr dev apply {proposal.id}")


@app.command(name="reject")
def reject(
    proposal_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b"),
    note: str = typer.Option("", "--note", "-n"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Recusa uma proposta."""

    runtime = get_runtime(workspace)
    proposal = _approve_or_reject(runtime, proposal_id, approved=False, actor=by, note=note)
    warning(f"{proposal.id} recusada")


@app.command(name="apply")
def apply(
    proposal_id: str = typer.Argument(...),
    by: str = typer.Option("human:cli", "--by", "-b"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Escreve a proposta aprovada no workspace e recarrega o Runtime."""

    runtime = get_runtime(workspace)
    try:
        proposal = runtime.workbench.apply(proposal_id, actor=by)
    except Exception as exc:
        error(f"{type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc
    success(f"{proposal.id} aplicada em {proposal.target}")


@app.command(name="tools")
def tools(workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Ferramentas carregadas do workspace (e as recusadas)."""

    runtime = get_runtime(workspace)
    data = runtime.dev_status()["workspace_tools"]
    files = data["files"]
    if files:
        kv("Ferramentas do workspace", {"arquivos": ", ".join(files), "diretório": data["dir"]})
    else:
        info(f"nenhuma ferramenta em {data['dir']}")
    rejected = data.get("rejected") or []
    if rejected:
        warning("recusadas no carregamento:")
        for item in rejected:
            for problem in item["problems"][:3]:
                info(f"  {item['file']}: {problem}")


# ---- helpers ----------------------------------------------------------


def _get(runtime, proposal_id: str):
    from ...core.errors import ConfigError

    try:
        return runtime.workbench.get(proposal_id)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc


def _approve_or_reject(runtime, proposal_id: str, *, approved: bool, actor: str, note: str):
    try:
        if approved:
            return runtime.workbench.approve(proposal_id, actor=actor, note=note)
        return runtime.workbench.reject(proposal_id, actor=actor, note=note)
    except Exception as exc:
        error(f"{type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc


def _show_checks(proposal) -> None:
    if not proposal.checks:
        return
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
            if not check.ok or check.level != "info"
        ]
        or [["ok", "tudo", "nenhum problema encontrado"]],
    )


__all__ = ["app"]
