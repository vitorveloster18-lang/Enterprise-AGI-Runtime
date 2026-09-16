"""egr task [objetivo] · egr task create|list|inspect|run|resume|cancel

`egr task \"<objetivo>\"` é o atalho do milestone: executa trabalho real.
O grupo roteia qualquer token desconhecido para o comando padrão `exec`.
"""

from __future__ import annotations

from pathlib import Path

import typer
from typer.core import TyperGroup

from ...domain.enums import TaskStatus
from ...domain.task import Task
from ..context import get_runtime
from ..formatting import code, error, info, json_output, kv, panel, success, table, warning


class DefaultCommandGroup(TyperGroup):
    """Grupo que trata o primeiro token desconhecido como comando padrão."""

    default_command = "exec"

    def resolve_command(self, ctx, args):
        if args and args[0] not in self.commands and self.default_command in self.commands:
            return self.default_command, self.commands[self.default_command], args
        return super().resolve_command(ctx, args)


app = typer.Typer(
    help="Tasks: a unidade de trabalho do Runtime",
    cls=DefaultCommandGroup,
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)


@app.callback(invoke_without_command=True)
def task_root(
    ctx: typer.Context,
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Sem argumentos lista as tasks; com texto, executa (ver `exec`)."""

    if ctx.invoked_subcommand is None:
        list_tasks(workspace=workspace)


@app.command(name="exec")
def exec_task(
    objective: list[str] = typer.Argument(..., help="Objetivo da task (executa imediatamente)"),
    agent: str = typer.Option(None, "--agent", "-a", help="Agente executor"),
    env: str = typer.Option(None, "--env", help="development | staging | production"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON"),
):
    """Executa uma task: `egr task \"Analise os documentos...\"`."""

    runtime = get_runtime(workspace)
    task = runtime.submit(" ".join(objective), agent_id=agent, environment=env)
    _render(runtime, task, as_json=as_json)
    if task.status == TaskStatus.REQUIRES_APPROVAL:
        warning("task aguardando aprovação humana")
        for approval in runtime.approvals.pending_for_task(task.id):
            info(f"aprovar: egr approval approve {approval.id}")
        raise typer.Exit(code=3)
    if task.status == TaskStatus.FAILED:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command(name="create")
def create(
    objective: list[str] = typer.Argument(..., help="Objetivo da task"),
    agent: str = typer.Option(None, "--agent", "-a"),
    env: str = typer.Option(None, "--env"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Cria e executa uma task."""

    runtime = get_runtime(workspace)
    task = runtime.submit(" ".join(objective), agent_id=agent, environment=env)
    _render(runtime, task, as_json=as_json)


@app.command(name="list")
def list_tasks(
    status: str = typer.Option(None, "--status", "-s"),
    env: str = typer.Option(None, "--env"),
    limit: int = typer.Option(20, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista tasks."""

    runtime = get_runtime(workspace)
    tasks = runtime.tasks.list(status=status, environment=env, limit=limit)
    if as_json:
        json_output([task.model_dump(mode="json") for task in tasks])
        return
    if not tasks:
        info("nenhuma task encontrada")
        return
    table(
        "Tasks",
        ["id", "status", "ambiente", "agente", "objetivo", "criada em"],
        [
            [
                task.id,
                task.status,
                task.environment,
                task.agent_id,
                task.objective[:60],
                task.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            ]
            for task in tasks
        ],
    )


@app.command(name="inspect")
def inspect(
    task_id: str = typer.Argument(..., help="Id da task"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    events: bool = typer.Option(False, "--events", help="Mostrar a trilha de auditoria"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Inspeciona uma task (passos, decisões, artefatos)."""

    runtime = get_runtime(workspace)
    task = runtime.tasks.get(task_id)
    if task is None:
        error(f"task {task_id} não encontrada")
        raise typer.Exit(code=1)
    if as_json:
        json_output(task.model_dump(mode="json"))
        return

    result = task.result
    kv(
        f"Task {task.id}",
        {
            "objetivo": task.objective,
            "status": task.status,
            "ambiente": task.environment,
            "agente": task.agent_id,
            "criada em": task.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "duração (ms)": (result.duration_ms if result else "-"),
            "modelo": (result.model if result else "-"),
            "erro": task.error or "-",
        },
    )
    if result and result.steps:
        table(
            "Passos",
            ["#", "ferramenta", "decisão", "regra", "ok", "ms", "erro"],
            [
                [
                    step.id,
                    step.tool,
                    step.decision,
                    step.rule_id or "-",
                    "sim" if step.ok else "não",
                    step.duration_ms,
                    (step.error or "")[:60],
                ]
                for step in result.steps
            ],
        )
    if result and result.answer:
        panel("Resposta", result.answer, style="green")
    if result and result.artifacts:
        artifacts = runtime.artifacts.list(task_id=task.id)
        table(
            "Artefatos",
            ["id", "nome", "caminho"],
            [[artifact.id, artifact.name, artifact.path or "-"] for artifact in artifacts],
        )
    pending = runtime.approvals.pending_for_task(task.id)
    if pending:
        warning(f"aprovações pendentes: {', '.join(item.id for item in pending)}")

    if events:
        audit_events = runtime.audit.list(task_id=task.id, limit=50)
        table(
            "Auditoria",
            ["seq", "evento", "ator", "detalhe"],
            [
                [event.seq, event.type, event.actor, str(event.payload)[:90]]
                for event in reversed(audit_events)
            ],
        )


@app.command(name="run")
def run(
    task_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Executa uma task existente (pending)."""

    runtime = get_runtime(workspace)
    task = runtime.tasks.get(task_id)
    if task is None:
        error(f"task {task_id} não encontrada")
        raise typer.Exit(code=1)
    task = runtime.agent_engine.run(task)
    _render(runtime, task)


@app.command(name="resume")
def resume(
    task_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Retoma uma task pausada em aprovação."""

    runtime = get_runtime(workspace)
    task = runtime.task_engine.resume(task_id)
    _render(runtime, task)
    success(f"task {task.id} retomada")


@app.command(name="cancel")
def cancel(
    task_id: str = typer.Argument(...),
    reason: str = typer.Option("cancelada pelo operador", "--reason"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cancela uma task."""

    runtime = get_runtime(workspace)
    task = runtime.task_engine.cancel(task_id, reason=reason)
    success(f"task {task.id} cancelada")


def _render(runtime, task: Task, as_json: bool = False) -> None:
    if as_json:
        json_output(task.model_dump(mode="json"))
        return
    result = task.result
    lines = [
        f"task     : {task.id}",
        f"status   : {task.status}",
        f"agente   : {task.agent_id}",
        f"ambiente : {task.environment}",
        f"modelo   : {(result.model if result else None) or 'local/echo'}",
        f"passos   : {len(result.steps) if result else 0}",
        f"artefatos: {len(result.artifacts) if result else 0}",
    ]
    panel(
        "Task executada",
        "\n".join(lines),
        style="green" if task.status == TaskStatus.COMPLETED else "yellow",
    )
    if result and result.steps:
        table(
            "Passos",
            ["#", "ferramenta", "decisão", "ok", "ms"],
            [
                [step.id, step.tool, step.decision, "sim" if step.ok else "não", step.duration_ms]
                for step in result.steps
            ],
        )
    if result and result.answer:
        code(result.answer[:1500], "markdown" if len(result.answer) > 200 else "text")
    if task.error:
        warning(task.error)


__all__ = ["app"]
