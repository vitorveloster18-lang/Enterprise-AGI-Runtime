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

CONFIG_HEADER = """# EGR — Enterprise AGI Runtime
#
# Este arquivo é a configuração do workspace. Segredos NUNCA ficam aqui:
# use `api_key_env` (nome de variável de ambiente) ou o placeholder ${env:VAR}.
#
# Exemplo de configuração de modelos (descomente e ajuste):
#
# models:
#   routing: cost                 # priority | cost | local_first
#   budget:
#     currency: USD
#     per_task: 0.50              # teto por task
#     per_day: 5.00               # teto por dia
#     on_exceeded: deny           # deny | warn
#   providers:
#     - name: local
#       type: ollama              # modelo local: os dados não saem da máquina
#       base_url: http://localhost:11434
#       model: llama3.1
#       capabilities: [reasoning, chat]
#       priority: 100
#     - name: cloud
#       type: openai_compat       # OpenAI, Groq, OpenRouter, vLLM, LM Studio...
#       base_url: https://api.openai.com/v1
#       api_key_env: OPENAI_API_KEY
#       model: gpt-4o-mini
#       external: true            # cruza a fronteira da empresa (Data Boundary)
#       priority: 10
#       pricing:                  # USD por 1M de tokens (base do orçamento)
#         input_per_1m: 0.15
#         output_per_1m: 0.60
#
# Ferramentas (Fase 3):
#
# tools:
#   sandbox:
#     mode: auto                  # auto | container | process
#     image: python:3.11-alpine
#     network: false              # contêiner sem rede
#     memory: 512m
#     cpus: '1'
#     pids_limit: 128
#   email:
#     enabled: false              # envio sempre exige aprovação humana
#     smtp_host: smtp.exemplo.com
#     username_env: EGR_SMTP_USER
#     password_env: EGR_SMTP_PASS
#     cost_per_send: 0.0
#   browser:
#     enabled: false              # requer playwright instalado
#     allowed_domains: []
#
# MCP — servidores externos entram como Tools (default deny até haver política):
#
# mcp:
#   enabled: true
#   servers:
#     - name: calculadora
#       command: python
#       args: [./servers/calculadora.py]
#
# Orquestração (Fase 6): cada passo do workflow é uma task auditada
#
#   egr workflow run <id>          # DAG, retry, condição e compensação
#   egr workflow schedule          # cron: o que está vencido
#   egr workflow tick              # idempotente: chame pelo cron do SO
#
# workflows/*.yaml aceita por passo: depends_on, condition, max_attempts,
# on_error (fail|continue|compensate), compensate_with, outputs e namespace.
#
# Memória (Fase 5): recuperação híbrida e ciclo de vida
#
# memory:
#   retrieval: hybrid          # hybrid | fts | semantic
#   semantic_weight: 1.0       # 0 desliga o lado semântico
#   min_cosine: 0.12           # corte de similaridade
#   half_life_days: 30         # decaimento da saliência
#   duplicate_threshold: 0.90  # cosseno para considerar near-duplicata
#   retention_days: 365        # arquivado só é podado depois disso
#
# Segurança (Fase 4): identidade, RBAC e cofre de segredos
#
# security:
#   identity_required: false     # true = decisões exigem principal autenticado
#   approval_min_role: approver  # papel mínimo de quem aprova
#   allow_agent_approval: false  # agente nunca aprova o próprio trabalho
#
# egr key init                                    # cria a chave mestra (0600)
# printf 'sk-...' | egr secret set openai --stdin # credencial cifrada no cofre
# egr identity add vitor --roles approver
# egr identity token vitor --ttl-days 30
#
"""


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
        config_file.write_text(CONFIG_HEADER + dump_config(config), encoding="utf-8")

    created = render_workspace(workspace, overwrite=force, sample=sample)
    info(f"{len(created)} arquivo(s) gravados em {workspace}")

    runtime = Runtime.load(workspace)
    counts = runtime.sync_all()  # agentes + políticas do YAML entram no Runtime
    synced = list(runtime.agents.values())
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
        f"workflows  : {counts['workflows']}",
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
        ["nome", "tipo", "modelo", "capacidades", "externo", "custo/1k tokens"],
        [
            [
                provider["name"],
                provider["type"],
                provider["model"] or "-",
                ", ".join(provider["capabilities"]),
                "sim" if provider["external"] else "não",
                f"{provider['unit_cost']:.6f}",
            ]
            for provider in data["models"]
        ],
    )
    spend = data["spend"]
    budget = data["budget"]
    kv(
        "Custo e orçamento",
        {
            "roteamento": data["routing"],
            "gasto hoje": f"{spend['today']['total_cost']:.6f} {budget['currency']}",
            "gasto total": f"{spend['total']['total_cost']:.6f} {budget['currency']}",
            "chamadas (total)": spend["total"]["calls"],
            "tokens (total)": f"{spend['total']['input_tokens']}/{spend['total']['output_tokens']}",
            "limite por dia": budget["per_day"] if budget["per_day"] is not None else "sem limite",
            "limite por task": budget["per_task"] if budget["per_task"] is not None else "sem limite",
            "ao estourar": budget["on_exceeded"],
        },
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
