"""egr mcp list|call · servidores MCP entram como Tools governadas.

Ferramentas MCP são descobertas em runtime e **não herdam permissão nenhuma**:
sem regra de política, o default deny bloqueia.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table, warning

app = typer.Typer(help="MCP: servidores externos como ferramentas do Runtime")


@app.command(name="list")
def list_mcp(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista servidores MCP configurados e as ferramentas descobertas."""

    runtime = get_runtime(workspace)
    config = runtime.settings.config.mcp
    tools = [tool for tool in runtime.tools.list() if tool["name"].startswith("mcp.")]

    if as_json:
        json_output({"servers": [server.model_dump() for server in config.servers], "tools": tools,
                     "failures": runtime.mcp_failures})
        return

    if not config.servers:
        info("nenhum servidor MCP configurado (mcp.servers no egr.yaml)")
        return

    table(
        "Servidores MCP",
        ["nome", "comando", "ativo", "ferramentas"],
        [
            [
                server.name,
                f"{server.command} {' '.join(server.args)}".strip(),
                "sim" if server.enabled else "não",
                len([tool for tool in tools if tool["name"].startswith(f"mcp.{server.name}.")]),
            ]
            for server in config.servers
        ],
    )
    if tools:
        table(
            "Ferramentas descobertas",
            ["nome", "risco", "descrição"],
            [[tool["name"], tool["risk"], tool["description"][:70]] for tool in tools],
        )
        warning("sem regra de política, ferramentas MCP são bloqueadas por default deny")
    if runtime.mcp_failures:
        for failure in runtime.mcp_failures:
            error(f"{failure['server']}: {failure['error']}")


@app.command(name="call")
def call(
    tool: str = typer.Argument(..., help="Ferramenta no formato mcp.<servidor>.<ferramenta>"),
    arg: list[str] = typer.Option([], "--arg", "-a", help="Argumentos chave=valor"),
    env: str = typer.Option(None, "--env"),
    agent: str = typer.Option(None, "--agent"),
    execute: bool = typer.Option(False, "--execute", "-x"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Avalia a política (e opcionalmente executa) uma ferramenta MCP."""

    from ..formatting import parse_kv

    runtime = get_runtime(workspace)
    args = parse_kv(arg)
    request, decision = runtime.request_action(tool, args, agent_id=agent, environment=env, rationale="egr mcp call")

    if as_json:
        json_output({"decision": decision.model_dump(mode="json"), "args": args})
    else:
        kv(
            f"Decisão — {tool}",
            {
                "decisão": decision.decision,
                "regra": decision.rule_id or "-",
                "motivo": decision.reason,
                "papel exigido": decision.required_role or "-",
                "argumentos": str(args),
            },
        )

    if decision.needs_approval:
        approval = runtime.request_approval(request, decision, agent_id=agent and runtime.resolve_agent(agent))
        info(f"aprovação criada: {approval.id} — `egr approval approve {approval.id}`")
        return
    if not decision.allowed:
        error("bloqueado pela política (default deny para ferramentas MCP)")
        raise typer.Exit(code=1)
    if not execute:
        info("use --execute para executar")
        return

    agent_spec = runtime.resolve_agent(agent) if agent else None
    ctx = runtime.adhoc_tool_context(environment=request.environment, task_id="cli", agent=agent_spec)
    result = runtime.tools.execute(tool, args, ctx)
    if as_json:
        json_output(result.model_dump(mode="json"))
        raise typer.Exit(code=0 if result.ok else 1)

    if result.ok:
        success(f"executado em {result.duration_ms} ms")
        kv("Resultado", {"saída": str(result.output)[:800], "custo": result.cost})
    else:
        error(result.error or "falha na execução")
        raise typer.Exit(code=1)


__all__ = ["app"]
