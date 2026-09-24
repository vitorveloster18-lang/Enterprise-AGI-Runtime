"""egr tool list|test|run · a fronteira entre o agente e o mundo."""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, json_output, kv, success, table, warning

app = typer.Typer(help="Ferramentas: execução governada")


@app.command(name="list")
def list_tools(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista as ferramentas registradas no Runtime."""

    runtime = get_runtime(workspace)
    tools = runtime.tools.list()
    if as_json:
        json_output(tools)
        return
    table(
        "Ferramentas",
        ["nome", "risco", "efeitos colaterais", "rede", "parâmetros"],
        [
            [
                tool["name"],
                tool["risk"],
                "sim" if tool["side_effects"] else "não",
                "sim" if tool["requires_network"] else "não",
                ", ".join(tool["parameters"].keys()) or "-",
            ]
            for tool in tools
        ],
    )


@app.command(name="test")
def test_tool(
    tool: str = typer.Argument(..., help="Nome da ferramenta"),
    arg: list[str] = typer.Option([], "--arg", "-a", help="Argumentos no formato chave=valor"),
    env: str = typer.Option(None, "--env", help="Ambiente para avaliação da política"),
    agent: str = typer.Option(None, "--agent", help="Agente solicitante"),
    execute: bool = typer.Option(False, "--execute", "-x", help="Executa após a autorização"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Testa uma ferramenta: avalia a política e (opcionalmente) executa."""

    from ..formatting import parse_kv

    runtime = get_runtime(workspace)
    args = parse_kv(arg)
    request, decision = runtime.request_action(
        tool,
        args,
        agent_id=agent,
        environment=env,
        rationale="egr tool test",
    )
    if as_json:
        json_output({"request": request.model_dump(mode="json"), "decision": decision.model_dump(mode="json")})
    else:
        kv(
            f"Avaliação de política — {tool}",
            {
                "decisão": decision.decision,
                "regra": decision.rule_id or "-",
                "política": decision.policy_id or "-",
                "motivo": decision.reason,
                "papel exigido": decision.required_role or "-",
                "argumentos": str(args),
            },
        )

    if not execute:
        if decision.needs_approval:
            warning("esta ação exigiria aprovação humana (use --execute para criar a aprovação)")
        return

    agent_spec = runtime.resolve_agent(agent) if agent else None

    if decision.needs_approval:
        approval = runtime.request_approval(
            request,
            decision,
            agent=agent_spec,
            step_id="cli",
            environment=request.environment,
        )
        info(f"aprovação criada: {approval.id} — `egr approval approve {approval.id}`")
        return
    if not decision.allowed:
        error("execução negada pela política")
        raise typer.Exit(code=1)

    ctx = runtime.adhoc_tool_context(environment=request.environment, task_id="cli", agent=agent_spec)
    result = runtime.tools.execute(tool, args, ctx)
    if as_json:
        json_output(result.model_dump(mode="json"))
    else:
        success(f"executado em {result.duration_ms} ms")
        kv("Resultado", {"ok": result.ok, "saída": str(result.output)[:800], "erro": result.error or "-"})


@app.command(name="run")
def run_tool(
    tool: str = typer.Argument(...),
    arg: list[str] = typer.Option([], "--arg", "-a"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Atalho para `tool test --execute`."""

    test_tool(tool, arg, None, None, True, workspace, False)


__all__ = ["app"]
