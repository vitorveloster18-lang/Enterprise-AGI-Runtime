"""egr workflow list|run · Workflow = como o trabalho acontece."""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, json_output, success, table, warning

app = typer.Typer(help="Workflows: processos executados por agentes")


@app.command(name="list")
def list_workflows(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista os workflows declarados em workflows/*.yaml."""

    runtime = get_runtime(workspace)
    runtime._load_workflows()
    workflows = list(runtime.workflows.values())
    if as_json:
        json_output([workflow.model_dump(mode="json") for workflow in workflows])
        return
    if not workflows:
        info("nenhum workflow declarado (crie arquivos em workflows/)")
        return
    table(
        "Workflows",
        ["id", "versão", "ambiente", "trigger", "passos"],
        [
            [workflow.id, workflow.version, workflow.environment, workflow.trigger.type, len(workflow.steps)]
            for workflow in workflows
        ],
    )


@app.command(name="run")
def run_workflow(
    workflow_id: str = typer.Argument(...),
    env: str = typer.Option(None, "--env"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    agent: str = typer.Option(None, "--agent", help="Sobrescreve o agente dos passos"),
):
    """Executa um workflow sequencialmente: cada passo vira uma task auditada."""

    runtime = get_runtime(workspace)
    runtime._load_workflows()
    workflow = runtime.workflows.get(workflow_id)
    if workflow is None:
        error(f"workflow '{workflow_id}' não encontrado")
        raise typer.Exit(code=1)

    environment = env or str(workflow.environment)
    results = []
    for step in workflow.steps:
        objective = step.objective or (f"Executar {step.tool} com os argumentos {step.args}" if step.tool else "")
        if not objective:
            warning(f"passo {step.id} sem objetivo: ignorado")
            continue
        task = runtime.submit(
            objective,
            agent_id=step.agent or agent,
            environment=environment,
        )
        results.append((step.id, task))

    table(
        f"Workflow {workflow.id}",
        ["passo", "task", "agente", "status", "passos executados"],
        [
            [
                step_id,
                task.id,
                task.agent_id,
                task.status,
                len(task.result.steps) if task.result else 0,
            ]
            for step_id, task in results
        ],
    )
    paused = [task for _, task in results if str(task.status) == "requires_approval"]
    if paused:
        warning(f"{len(paused)} task(s) aguardando aprovação humana")
    success(f"workflow {workflow_id} executado em {environment}")


__all__ = ["app"]
