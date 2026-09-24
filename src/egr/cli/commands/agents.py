"""egr agent list|create|show|run|sync"""

from __future__ import annotations

from pathlib import Path

import typer

from ...domain.agent import AgentSpec, ModelSpec
from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table

app = typer.Typer(help="Agentes: unidades cognitivas do Runtime")


@app.command(name="list")
def list_agents(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista os agentes registrados."""

    runtime = get_runtime(workspace)
    agents = list(runtime.agents.values())
    if as_json:
        json_output([agent.model_dump(mode="json") for agent in agents])
        return
    if not agents:
        info("nenhum agente registrado (use `egr agent create` ou `egr agent sync`)")
        return
    table(
        "Agentes",
        ["id", "versão", "ambiente", "capacidade", "ferramentas", "memória"],
        [
            [
                agent.id,
                agent.version,
                agent.environment,
                agent.model.capability,
                ", ".join(agent.permissions.tools) or "(herda da política)",
                ", ".join(agent.memory),
            ]
            for agent in agents
        ],
    )


@app.command(name="show")
def show(
    agent_id: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Mostra a definição de um agente."""

    runtime = get_runtime(workspace)
    agent = runtime.agents.get(agent_id) or runtime.agent_repository.get(agent_id)
    if agent is None:
        error(f"agente '{agent_id}' não encontrado")
        raise typer.Exit(code=1)
    if as_json:
        json_output(agent.model_dump(mode="json"))
        return
    kv(
        f"Agente {agent.id}",
        {
            "versão": agent.version,
            "objetivo": agent.objective,
            "capacidade": agent.model.capability,
            "modelo": agent.model.model or "(routing automático)",
            "ferramentas": ", ".join(agent.permissions.tools) or "(herda da política)",
            "namespaces": ", ".join(agent.memory),
            "ambiente": agent.environment,
            "risco máximo": agent.permissions.max_risk,
        },
    )


@app.command(name="create")
def create(
    agent_id: str = typer.Argument(..., help="Id do agente"),
    objective: str = typer.Option("", "--objective", "-o", help="Objetivo do agente"),
    capability: str = typer.Option("reasoning", "--capability", "-c"),
    memory: str = typer.Option("default", "--memory", "-m", help="Namespaces separados por vírgula"),
    tools: str = typer.Option("", "--tools", "-t", help="Ferramentas permitidas (ex: filesystem.*,python.execute)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Cria (ou atualiza) um agente."""

    runtime = get_runtime(workspace)
    spec = AgentSpec(
        id=agent_id,
        objective=objective or f"Agente {agent_id}",
        model=ModelSpec(capability=capability),
        memory=[item.strip() for item in memory.split(",") if item.strip()],
    )
    if tools:
        spec.permissions.tools = [item.strip() for item in tools.split(",") if item.strip()]
    runtime.register_agent(spec)
    success(f"agente '{agent_id}' registrado (v{spec.version})")


@app.command(name="run")
def run(
    agent_id: str = typer.Argument(...),
    objective: str = typer.Argument(..., help="Objetivo da task"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    env: str = typer.Option(None, "--env"),
):
    """Executa uma task com um agente específico."""

    runtime = get_runtime(workspace)
    task = runtime.submit(objective, agent_id=agent_id, environment=env)
    kv(
        "Task",
        {
            "id": task.id,
            "agente": task.agent_id,
            "status": task.status,
            "passos": len(task.result.steps) if task.result else 0,
        },
    )


@app.command(name="sync")
def sync(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Carrega agents/*.yaml para dentro do Runtime."""

    runtime = get_runtime(workspace)
    agents = runtime.sync_agents()
    success(f"{len(agents)} agente(s) sincronizados: {', '.join(agent.id for agent in agents) or '-'}")


__all__ = ["app"]
