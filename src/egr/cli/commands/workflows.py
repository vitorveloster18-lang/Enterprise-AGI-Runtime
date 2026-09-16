"""egr workflow · Workflow = como o trabalho acontece (Fase 6).

Cada passo vira uma task auditada; a execução (run) é um objeto persistido,
retomável e cancelável.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from ...core.errors import ConfigError
from ...core.timeutil import utcnow
from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table, warning

app = typer.Typer(help="Workflows: DAG, retry, compensação, agenda e webhooks")


def _load(runtime):
    runtime._load_workflows()
    return runtime.workflows


@app.command(name="list")
def list_workflows(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista os workflows declarados."""

    runtime = get_runtime(workspace)
    workflows = list(_load(runtime).values())
    if as_json:
        json_output([workflow.model_dump(mode="json") for workflow in workflows])
        return
    if not workflows:
        info("nenhum workflow declarado (crie arquivos em workflows/)")
        return
    table(
        "Workflows",
        ["id", "versão", "ambiente", "trigger", "quando", "passos", "paralelo"],
        [
            [
                workflow.id,
                workflow.version,
                workflow.environment,
                workflow.trigger.type,
                workflow.trigger.cron or workflow.trigger.event or "-",
                len(workflow.steps),
                "sim" if workflow.parallel else "não",
            ]
            for workflow in workflows
        ],
    )
    problems = {
        workflow.id: runtime.orchestrator.validate(workflow)
        for workflow in workflows
        if runtime.orchestrator.validate(workflow)
    }
    if problems:
        warning("workflows com problemas de declaração:")
        for workflow_id, items in problems.items():
            for item in items:
                info(f"  · {workflow_id}: {item}")


@app.command(name="validate")
def validate(
    workflow_id: str = typer.Argument(None),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Valida a declaração (DAG, dependências, condições e cron)."""

    runtime = get_runtime(workspace)
    workflows = _load(runtime)
    targets = [workflows[workflow_id]] if workflow_id else list(workflows.values())
    if workflow_id and workflow_id not in workflows:
        error(f"workflow '{workflow_id}' não encontrado")
        raise typer.Exit(code=1)

    report = {workflow.id: runtime.orchestrator.validate(workflow) for workflow in targets}
    if as_json:
        json_output(report)
        raise typer.Exit(code=0 if not any(report.values()) else 1)

    total = sum(len(items) for items in report.values())
    if not total:
        success("todos os workflows são válidos")
        return
    table(
        "Problemas",
        ["workflow", "problema"],
        [[workflow_id, item] for workflow_id, items in report.items() for item in items],
    )
    raise typer.Exit(code=1)


@app.command(name="run")
def run_workflow(
    workflow_id: str = typer.Argument(...),
    env: str = typer.Option(None, "--env"),
    agent: str = typer.Option(None, "--agent", help="Sobrescreve o agente de todos os passos"),
    input_values: list[str] = typer.Option(None, "--input", "-i", help="chave=valor (repetível)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Executa um workflow: cada passo vira uma task auditada."""

    runtime = get_runtime(workspace)
    if agent:
        for workflow in _load(runtime).values():
            if workflow.id == workflow_id:
                for step in workflow.steps:
                    step.agent = step.agent or agent
    try:
        run = runtime.run_workflow(
            workflow_id,
            inputs=_parse_inputs(input_values),
            environment=env,
            created_by="cli",
        )
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    if as_json:
        json_output(run.model_dump(mode="json"))
        raise typer.Exit(code=0 if str(run.status) in ("completed", "partial") else 1)
    _render_run(runtime, run)


@app.command(name="runs")
def list_runs(
    workflow_id: str = typer.Option(None, "--workflow", "-W"),
    status: str = typer.Option(None, "--status", "-s"),
    limit: int = typer.Option(20, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Histórico de execuções."""

    runtime = get_runtime(workspace)
    runs = runtime.runs.list(workflow_id=workflow_id, status=status, limit=limit)
    if as_json:
        json_output([run.model_dump(mode="json") for run in runs])
        return
    if not runs:
        info("nenhuma execução registrada")
        return
    table("Execuções", ["id", "workflow", "status", "trigger", "passos", "custo", "criado em"],
          [[run.id, run.workflow_id, run.status, run.trigger,
            f"{run.summary['completed']}/{run.summary['total']}",
            f"{run.cost:.6f}", run.created_at.strftime("%Y-%m-%d %H:%M")] for run in runs])


@app.command(name="inspect")
def inspect(
    run_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Mostra uma execução passo a passo."""

    runtime = get_runtime(workspace)
    run = runtime.runs.get(run_id)
    if run is None:
        error(f"execução {run_id} não encontrada")
        raise typer.Exit(code=1)
    if as_json:
        json_output(run.model_dump(mode="json"))
        return
    _render_run(runtime, run)


@app.command(name="resume")
def resume(
    run_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Retoma uma execução pausada (ex.: aprovação decidida)."""

    runtime = get_runtime(workspace)
    try:
        run = runtime.orchestrator.resume(run_id)
    except KeyError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"execução {run.id} → {run.status}")
    if as_json:
        json_output(run.model_dump(mode="json"))
        return
    _render_run(runtime, run)


@app.command(name="cancel")
def cancel(
    run_id: str = typer.Argument(...),
    reason: str = typer.Option("cancelado pelo operador", "--reason", "-r"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cancela uma execução e as tasks abertas dela."""

    runtime = get_runtime(workspace)
    try:
        run = runtime.orchestrator.cancel(run_id, reason=reason)
    except KeyError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc
    success(f"execução {run.id} cancelada")


@app.command(name="triggers")
def triggers(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Gatilhos declarados (evento/cron/webhook)."""

    runtime = get_runtime(workspace)
    _load(runtime)
    from ...runtime.triggers import planned

    rows = planned(runtime)
    scheduled = runtime.orchestration_status()["triggers"]
    if as_json:
        json_output({"events": rows, "declared": scheduled})
        return
    if rows:
        table("Gatilhos por evento", ["workflow", "evento", "ambiente", "ativo"],
              [[row["workflow"], row["evento"], row["ambiente"], "sim" if row["ativo"] else "não"] for row in rows])
    cron_rows = [item for item in scheduled if item["type"] == "cron"]
    if cron_rows:
        table("Agendados (cron)", ["workflow", "cron", "ativo"],
              [[item["workflow"], item["cron"] or "-", "sim" if item["enabled"] else "não"] for item in cron_rows])


@app.command(name="schedule")
def schedule(
    limit: int = typer.Option(5, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """O que está vencido agora e os próximos disparos."""

    runtime = get_runtime(workspace)
    due = runtime.scheduler.due()
    upcoming = runtime.scheduler.upcoming(limit=limit)
    if as_json:
        json_output({"due": due, "upcoming": upcoming})
        return
    kv("Agenda", {"agora": utcnow().replace(second=0, microsecond=0).isoformat()})
    if due:
        table("Vencidos agora", ["workflow", "cron", "disparo"],
              [[item["workflow"], item.get("cron", "-"), item.get("due_at", "-")]
               for item in due if "error" not in item])
    else:
        info("nenhum agendamento vencido neste minuto")
    for item in due:
        if "error" in item:
            warning(f"{item['workflow']}: {item['error']}")
    if upcoming:
        table("Próximos", ["workflow", "cron", "próximo disparo"],
              [[item["workflow"], item["cron"], item.get("next_at") or "-"] for item in upcoming])


@app.command(name="tick")
def tick(
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra sem executar"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Executa os agendamentos vencidos (idempotente: feito para o cron do SO)."""

    runtime = get_runtime(workspace)
    started = runtime.scheduler.tick(dry_run=dry_run)
    if as_json:
        json_output([item if isinstance(item, dict) else item.model_dump(mode="json") for item in started])
        return
    if not started:
        info("nada a executar neste minuto")
        return
    for item in started:
        if isinstance(item, dict):
            success(f"executaria {item['workflow']} (cron {item.get('cron')})")
        else:
            success(f"{item.workflow_id} → {item.id} ({item.status})")


@app.command(name="daemon")
def daemon(
    interval: int = typer.Option(60, "--interval", "-i", help="Segundos entre ticks"),
    ticks: int = typer.Option(None, "--ticks", "-t", help="Parar após N ticks (padrão: nunca)"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Laço de agendamento para desenvolvimento (em produção, use o cron do SO)."""

    runtime = get_runtime(workspace)
    info(f"scheduler ativo a cada {interval}s (Ctrl+C para sair)")
    try:
        for batch in runtime.scheduler.loop(interval_seconds=interval, max_ticks=ticks, dry_run=dry_run):
            for item in batch:
                if isinstance(item, dict):
                    success(f"executaria {item['workflow']} (cron {item.get('cron')})")
                else:
                    success(f"{item.workflow_id} → {item.id} ({item.status})")
            time.sleep(0)  # cede o laço sem mascarar interrupção
    except KeyboardInterrupt:
        warning("scheduler interrompido")


def _parse_inputs(pairs: list[str] | None) -> dict:
    values: dict = {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        if key.strip():
            values[key.strip()] = value
    return values


def _render_run(runtime, run) -> None:
    kv(
        f"Execução {run.id}",
        {
            "workflow": f"{run.workflow_id}@{run.workflow_version}",
            "ambiente": str(run.environment),
            "status": str(run.status),
            "trigger": f"{run.trigger}{f' ({run.trigger_detail})' if run.trigger_detail else ''}",
            "passos": f"{run.summary['completed']} ok · {run.summary['failed']} falhos · "
            f"{run.summary['skipped']} pulados · {run.summary['waiting']} aguardando",
            "custo": f"{run.cost:.6f}",
            "erro": run.error or "-",
        },
    )
    table(
        "Passos",
        ["passo", "status", "task", "tentativas", "erro"],
        [
            [step.id, step.status, step.task_id or "-", step.attempts, (step.error or "-")[:60]]
            for step in run.steps
        ],
    )
    if str(run.status) == "waiting":
        pending = runtime.approvals.list(status="pending", limit=10)
        if pending:
            warning("aprovações pendentes:")
            for approval in pending:
                info(f"  · {approval.id} — {approval.tool} ({approval.required_role})")


__all__ = ["app"]
