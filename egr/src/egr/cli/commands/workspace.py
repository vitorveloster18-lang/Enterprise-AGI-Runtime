"""egr init · egr status · egr doctor · egr serve"""

from __future__ import annotations

from pathlib import Path

import typer

from ...core.config import EGRConfig, dump_config
from ...core.paths import find_workspace_root
from ...domain.enterprise import Enterprise, EnterpriseSettings
from ...runtime.runtime import Runtime, default_agents
from ...templates import render_workspace
from ..context import get_runtime
from ..formatting import error, info, json_output, kv, panel, success, table, warning

app = typer.Typer(help="Workspace: inicializar, inspecionar e diagnosticar o Runtime")


@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Diretório do workspace"),
    enterprise: str = typer.Option("local", "--enterprise", "-e", help="Id da enterprise"),
    name: str = typer.Option("", "--name", "-n", help="Nome da enterprise"),
    sample: bool = typer.Option(True, "--sample/--no-sample", help="Criar documentos de exemplo"),
    force: bool = typer.Option(False, "--force", help="Reescrever arquivos existentes"),
):
    """Cria a estrutura do workspace EGR (Fase 0)."""

    workspace = Path(path).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    config_file = workspace / "egr.yaml"
    if config_file.exists() and not force:
        warning(f"workspace já existe em {workspace} (use --force para reescrever)")
    else:
        config = EGRConfig(
            enterprise=Enterprise(
                id=enterprise,
                name=name or enterprise.capitalize(),
                settings=EnterpriseSettings(),
            )
        )
        config_file.write_text(dump_config(config), encoding="utf-8")

    created = render_workspace(workspace, overwrite=force, sample=sample)
    info(f"{len(created)} arquivo(s) gravados em {workspace}")

    runtime = Runtime.load(workspace)
    synced = runtime.sync_agents()
    runtime.audit.record(
        "system.event",
        actor="cli",
        payload={"action": "init", "workspace": str(workspace), "files": len(created)},
    )
    success(f"workspace inicializado: {workspace}")
    console_lines = [
        f"enterprise : {runtime.settings.enterprise.id}",
        f"ambiente   : {runtime.settings.environment}",
        f"agentes    : {', '.join(agent.id for agent in synced) or 'default'}",
        f"ferramentas: {len(runtime.tools.list())}",
        f"politicas  : {len(runtime.policy.list_policies())}",
        "",
        "próximo passo:",
        '  egr task "Analise os documentos desta pasta e produza um relatório."',
    ]
    panel("EGR Runtime", "\n".join(console_lines), style="green")


@app.command()
def status(
    workspace: Path | None = typer.Option(None, "--workspace", "-w", help="Caminho do workspace"),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON"),
):
    """Mostra o estado do Runtime."""

    runtime = get_runtime(workspace)
    data = runtime.status()
    if as_json:
        json_output(data)
        return

    counts = data["counts"]
    kv(
        "EGR Runtime",
        {
            "workspace": data["workspace"],
            "environment": data["environment"],
            "enterprise": f"{data['enterprise']['id']} ({data['enterprise']['name']})",
            "data_residency": data["enterprise"]["settings"]["data_residency"],
            "external_ai": data["enterprise"]["settings"]["external_ai"],
            "database": data["database"]["path"],
            "migrations": f"{len(data['database']['migrations']['applied'])} aplicadas",
        },
    )
    table(
        "Objetos",
        ["objeto", "quantidade"],
        [
            ["agents", counts["agents"]],
            ["tools", counts["tools"]],
            ["policies", counts["policies"]],
            ["artifacts", counts["artifacts"]],
            ["events (audit)", counts["events"]],
            ["memory", counts["memory"]["total"]],
            ["approvals pendentes", counts["approvals_pending"]],
        ],
    )
    if counts["tasks"]:
        table("Tasks", ["status", "total"], [[status, total] for status, total in counts["tasks"].items()])
    table(
        "Model providers",
        ["nome", "tipo", "modelo", "capacidades", "externo"],
        [
            [
                provider["name"],
                provider["type"],
                provider["model"] or "-",
                ", ".join(provider["capabilities"]),
                "sim" if provider["external"] else "não",
            ]
            for provider in data["models"]
        ],
    )


@app.command()
def doctor(
    workspace: Path | None = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Diagnóstico de saúde do Runtime (infra, políticas, modelos, auditoria)."""

    runtime = get_runtime(workspace)
    health = runtime.health()
    if as_json:
        json_output(health)
        raise typer.Exit(code=0 if health["healthy"] else 1)

    table(
        "egr doctor",
        ["check", "status", "detalhe"],
        [
            [check["check"], "OK" if check["ok"] else "FALHA", check["detail"]]
            for check in health["checks"]
        ],
    )
    if health["healthy"]:
        success("Runtime saudável")
        raise typer.Exit(code=0)
    error("Runtime com problemas: resolva os checks acima")
    raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Host de escuta"),
    port: int = typer.Option(8000, "--port", "-p", help="Porta"),
    workspace: Path | None = typer.Option(None, "--workspace", "-w"),
    reload: bool = typer.Option(False, "--reload"),
):
    """Sobe a API local do Runtime (FastAPI + console)."""

    import uvicorn

    from ...api.server import create_app

    runtime = get_runtime(workspace)
    api = create_app(runtime)
    info(f"EGR API em http://{host}:{port} (workspace: {runtime.settings.workspace})")
    uvicorn.run(api, host=host, port=port, reload=reload)


__all__ = ["app", "default_agents", "doctor", "find_workspace_root", "init", "serve", "status"]
