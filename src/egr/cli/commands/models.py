"""egr model list|health|test · o agente nunca fala direto com o fornecedor."""

from __future__ import annotations

from pathlib import Path

import typer

from ...models.gateway import CompletionRequest, Message
from ..context import get_runtime
from ..formatting import error, json_output, kv, table

app = typer.Typer(help="Model Gateway: roteamento por capacidade, custo, latência e privacidade")


@app.command(name="list")
def list_models(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista os providers configurados."""

    runtime = get_runtime(workspace)
    providers = runtime.gateway.list_providers()
    if as_json:
        json_output(providers)
        return
    table(
        "Model providers",
        ["nome", "tipo", "modelo", "capacidades", "prioridade", "externo", "ativo"],
        [
            [
                provider["name"],
                provider["type"],
                provider["model"] or "-",
                ", ".join(provider["capabilities"]),
                provider["priority"],
                "sim" if provider["external"] else "não",
                "sim" if provider["enabled"] else "não",
            ]
            for provider in providers
        ],
    )


@app.command(name="health")
def health(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Verifica a disponibilidade de cada provider."""

    runtime = get_runtime(workspace)
    report = runtime.gateway.health()
    if as_json:
        json_output(report)
        return
    table(
        "Saúde dos providers",
        ["provider", "status", "detalhe"],
        [[name, "OK" if data["healthy"] else "INDISPONÍVEL", data["detail"]] for name, data in report.items()],
    )


@app.command(name="usage")
def usage(
    task: str = typer.Option(None, "--task", "-t", help="Custo de uma task específica"),
    since: str = typer.Option(None, "--since", help="A partir de YYYY-MM-DD"),
    limit: int = typer.Option(20, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Quanto o Runtime gastou: chamadas, tokens, latência e custo."""

    runtime = get_runtime(workspace)
    data = runtime.usage.totals(task_id=task, since=since)
    if as_json:
        json_output(data)
        return

    kv(
        f"Custo de modelos {'da task ' + task if task else 'do workspace'}",
        {
            "chamadas": data["calls"],
            "custo total": f"{data['total_cost']:.6f}",
            "tokens entrada": data["input_tokens"],
            "tokens saída": data["output_tokens"],
            "latência média (ms)": data["avg_latency_ms"],
            "orçamento/dia": (
                f"{runtime.settings.config.models.budget.per_day} "
                f"{runtime.settings.config.models.budget.currency}"
                if runtime.settings.config.models.budget.per_day is not None
                else "sem limite"
            ),
            "estratégia de roteamento": runtime.settings.config.models.routing,
        },
    )
    if data["by_provider"]:
        table(
            "Por provider",
            ["provider", "chamadas", "custo", "tokens (in/out)", "latência média"],
            [
                [
                    item["provider"],
                    item["calls"],
                    f"{item['cost']:.6f}",
                    f"{item['input_tokens']}/{item['output_tokens']}",
                    int(item["avg_latency"] or 0),
                ]
                for item in data["by_provider"]
            ],
        )
    recent = runtime.usage.recent(limit=limit)
    if recent:
        table(
            "Chamadas recentes",
            ["quando", "provider", "modelo", "capacidade", "ms", "tokens", "custo", "status"],
            [
                [
                    (item["created_at"] or "")[11:19],
                    item["provider"],
                    item["model"] or "-",
                    item["capability"] or "-",
                    item["latency_ms"],
                    f"{item['input_tokens']}/{item['output_tokens']}",
                    f"{item['cost']:.6f}",
                    item["status"],
                ]
                for item in recent
            ],
        )


@app.command(name="test")
def test_model(
    prompt: str = typer.Option("Responda apenas: OK", "--prompt", "-p"),
    provider: str = typer.Option(None, "--provider", help="Forçar um provider"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Executa um completion de teste via Model Gateway."""

    runtime = get_runtime(workspace)
    gateway = runtime.gateway
    if provider:
        gateway = type(gateway)(
            [item for item in gateway.providers if item.name == provider],
            audit=runtime.audit,
            external_ai=runtime.settings.enterprise.settings.external_ai,
        )
    try:
        response = gateway.complete(
            CompletionRequest(messages=[Message(role="user", content=prompt)], capability="reasoning")
        )
    except Exception as exc:
        error(f"falha no Model Gateway: {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc

    kv(
        "Model Gateway",
        {
            "provider": response.provider,
            "modelo": response.model,
            "latência (ms)": response.latency_ms,
            "externo": "sim" if response.external else "não",
            "uso": str(response.usage),
            "resposta": response.text[:600],
        },
    )


__all__ = ["app"]
