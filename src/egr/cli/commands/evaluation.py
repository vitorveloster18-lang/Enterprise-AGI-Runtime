"""Evaluation: provar que o artefato continua bom (Fase 8).

    egr eval smoke tool exemplo.soma   # gera e roda a suíte mínima
    egr eval run <suíte>               # executa com limites e baseline
    egr eval runs | show <run>         # histórico medido
    egr eval baseline <run>            # promove uma execução a referência
    egr eval security tool exemplo.soma
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import error, info, kv, panel, success, table, warning

app = typer.Typer(no_args_is_help=True, help="Evaluation: casos, métricas, baseline, regressão e segurança")

TARGETS = ["tool", "workflow", "agent", "policy"]


def _target(value: str) -> str:
    value = (value or "").strip().lower()
    if value not in TARGETS:
        error(f"alvo inválido: {value} (use {', '.join(TARGETS)})")
        raise typer.Exit(code=2)
    return value


def _suite(runtime, suite_id: str):

    suite = runtime.evaluation_suites.get(suite_id) or runtime.suites.get(suite_id)
    if suite is None:
        error(f"suíte não encontrada: {suite_id}")
        raise typer.Exit(code=1)
    return suite


def _show_run(run, *, show_cases: bool = True) -> None:
    style = {"passed": "green", "failed": "red", "regressed": "yellow", "error": "red"}.get(str(run.status), "cyan")
    summary = run.summary()
    kv(
        f"Avaliação {run.id}",
        {
            "suíte": f"{run.suite_id}@{run.suite_version}",
            "alvo": summary["target"],
            "veredito": str(run.status),
            "casos": summary["cases"],
            "taxa de acerto": f"{run.pass_rate:.0%}",
            "custo": f"{float(run.metrics.get('total_cost', 0.0)):.6f}",
            "latência média": f"{run.metrics.get('avg_duration_ms', 0)} ms",
            "p95": f"{run.metrics.get('p95_duration_ms', 0)} ms",
            "baseline": run.baseline_run or "-",
            "regressões": summary["regressions"],
            "achados": f"{len(run.findings)} ({summary['blocking_findings']} críticos)",
            "ambiente": str(run.environment),
        },
    )
    if run.reasons:
        panel("Motivos", "\n".join(f"• {reason}" for reason in run.reasons), style=style)
    if run.comparison and (run.comparison.notes or run.comparison.new_failures):
        panel(
            "Comparação com a baseline",
            f"baseline: {run.comparison.baseline_run}\n"
            f"taxa de acerto: {run.comparison.pass_rate_delta:+.1%}\n"
            f"custo: {run.comparison.cost_delta:+.6f}\n"
            f"latência: {run.comparison.latency_drift_pct:+.1f}%\n"
            + "\n".join(f"• {note}" for note in run.comparison.notes),
            style="yellow" if run.comparison.regressions else "green",
        )
    if run.findings:
        table(
            "Varredura de segurança",
            ["severidade", "código", "detalhe"],
            [
                [str(finding.severity), finding.code, finding.detail[:90]]
                for finding in run.findings
            ],
        )
    if show_cases:
        table(
            "Casos",
            ["caso", "resultado", "duração", "custo", "verificações"],
            [
                [
                    case.name or case.case_id,
                    "ok" if case.ok else "FALHOU",
                    f"{case.duration_ms} ms",
                    f"{case.cost:.6f}",
                    "; ".join(
                        f"{check.expression}={'ok' if check.ok else 'FALHOU'}" for check in case.checks
                    )[:70]
                    or (case.error or "-"),
                ]
                for case in run.cases
            ],
        )


@app.command(name="smoke")
def smoke(
    target_kind: str = typer.Argument(..., help="tool | workflow | agent | policy"),
    target: str = typer.Argument(..., help="nome/id do artefato"),
    keep: bool = typer.Option(True, "--keep/--no-keep", help="guardar a suíte gerada"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Gera a suíte mínima a partir da declaração do artefato e roda."""

    from ...core.errors import ConfigError
    from ...evaluation.suites import smoke_suite

    runtime = get_runtime(workspace)
    kind = _target(target_kind)
    try:
        suite = smoke_suite(runtime, kind, target)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    existing = runtime.evaluation_suites.get(suite.id)
    suite = existing or suite
    info(f"suíte {suite.id} · {len(suite.cases)} caso(s) derivado(s) da declaração")

    try:
        run = runtime.evaluator.run(suite, actor="cli")
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    if keep:
        runtime.suites.save(suite)
        runtime.evaluation_suites[suite.id] = suite
        info(f"suíte guardada: {suite.id} (evaluations/ para versionar)")
    _show_run(run)


@app.command(name="run")
def run(
    suite_id: str = typer.Argument(..., help="id da suíte"),
    baseline: str = typer.Option(None, "--baseline", "-b", help="id da execução usada como referência"),
    as_json: bool = typer.Option(False, "--json"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Executa uma suíte: casos, métricas, limites e comparação."""

    from ...core.errors import ConfigError

    runtime = get_runtime(workspace)
    suite = _suite(runtime, suite_id)
    try:
        run_result = runtime.evaluator.run(suite, baseline=baseline, actor="cli")
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(run_result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))
    else:
        _show_run(run_result)
    if not run_result.acceptable:
        raise typer.Exit(code=1)


@app.command(name="add")
def add(
    path: Path = typer.Argument(..., help="arquivo YAML com a suíte"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Registra uma suíte declarada em YAML (copie para evaluations/ para versionar)."""

    from ...core.errors import ConfigError
    from ...evaluation.loader import load_suite_file

    runtime = get_runtime(workspace)
    try:
        suites = load_suite_file(path)
    except ConfigError as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    for suite in suites:
        runtime.suites.save(suite)
        runtime.evaluation_suites[suite.id] = suite
        success(f"suíte {suite.id} registrada ({len(suite.cases)} caso(s)) → {suite.target_kind}:{suite.target}")


@app.command(name="list")
def list_suites(
    as_json: bool = typer.Option(False, "--json"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Suítes conhecidas."""

    runtime = get_runtime(workspace)
    suites = list(runtime.evaluation_suites.values())
    if as_json:
        payload = [suite.model_dump(mode="json") for suite in suites]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    if not suites:
        info("nenhuma suíte — `egr eval smoke tool <nome>` gera a mínima")
        return
    table(
        "Suítes",
        ["id", "alvo", "casos", "acerto mínimo", "limites"],
        [
            [
                suite.id,
                f"{suite.target_kind}:{suite.target}",
                len(suite.cases),
                f"{suite.thresholds.min_pass_rate:.0%}",
                ", ".join(
                    part
                    for part in (
                        f"custo≤{suite.thresholds.max_total_cost}" if suite.thresholds.max_total_cost else "",
                        f"p95≤{suite.thresholds.max_p95_duration_ms}ms"
                        if suite.thresholds.max_p95_duration_ms
                        else "",
                        f"regressões≤{suite.thresholds.max_regressions}",
                    )
                    if part
                ),
            ]
            for suite in suites
        ],
    )


@app.command(name="runs")
def runs(
    suite_id: str = typer.Option(None, "--suite", "-s"),
    status: str = typer.Option(None, "--status"),
    limit: int = typer.Option(20, "--limit", "-l"),
    as_json: bool = typer.Option(False, "--json", help="saída completa em JSON (ids sem truncar)"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Histórico das execuções medidas."""

    runtime = get_runtime(workspace)
    items = runtime.evaluations.list(suite_id=suite_id, status=status, limit=limit)
    if not items:
        info("nenhuma execução registrada")
        return
    if as_json:
        payload = [item.model_dump(mode="json") for item in items]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    table(
        "Execuções",
        ["id", "suíte", "alvo", "veredito", "acerto", "custo", "p95", "baseline"],
        [
            [
                item.id,
                item.suite_id,
                f"{item.target_kind}:{item.target}",
                str(item.status),
                f"{len(item.passed_cases)}/{len(item.cases)}",
                f"{float(item.metrics.get('total_cost', 0.0)):.6f}",
                f"{item.metrics.get('p95_duration_ms', 0)} ms",
                (item.baseline_run or "-"),
            ]
            for item in items
        ],
    )


@app.command(name="show")
def show(run_id: str = typer.Argument(...), workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Mostra uma execução: casos, métricas, comparação e achados."""

    runtime = get_runtime(workspace)
    run = runtime.evaluations.get(run_id)
    if run is None:
        error(f"execução não encontrada: {run_id}")
        raise typer.Exit(code=1)
    _show_run(run)


@app.command(name="baseline")
def baseline(run_id: str = typer.Argument(...), workspace: Path = typer.Option(None, "--workspace", "-w")):
    """Promove uma execução aprovada a referência (baseline) da suíte."""

    runtime = get_runtime(workspace)
    run = runtime.evaluations.get(run_id)
    if run is None:
        error(f"execução não encontrada: {run_id}")
        raise typer.Exit(code=1)
    if run.status != "passed":
        warning(f"execução {run.id} tem veredito '{run.status}' — baseline de algo reprovado vira referência torta")
        if not typer.confirm("promover mesmo assim?"):
            raise typer.Exit(code=0)

    suite = runtime.evaluation_suites.get(run.suite_id) or runtime.suites.get(run.suite_id)
    if suite is None:
        error(f"suíte {run.suite_id} não encontrada")
        raise typer.Exit(code=1)
    suite.metadata["baseline_run"] = run.id
    runtime.suites.save(suite)
    runtime.evaluation_suites[suite.id] = suite
    success(f"{run.id} é agora a baseline de {suite.id}")


@app.command(name="security")
def security(
    target_kind: str = typer.Argument(...),
    target: str = typer.Argument(...),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Varredura de segurança do artefato (permissões, políticas, declaração)."""

    from ...evaluation.security import scan

    runtime = get_runtime(workspace)
    kind = _target(target_kind)
    findings = scan(runtime, kind, target)
    if not findings:
        success(f"nada a declarar em {kind}:{target}")
        return
    table(
        f"Segurança — {kind}:{target}",
        ["severidade", "código", "detalhe"],
        [[str(finding.severity), finding.code, finding.detail[:100]] for finding in findings],
    )
    if any(finding.blocking for finding in findings):
        raise typer.Exit(code=1)


@app.command(name="judge")
def judge(
    suite_id: str = typer.Argument(..., help="id da suíte"),
    method: str = typer.Option("auto", "--method", "-m", help="auto | similaridade | modelo"),
    as_json: bool = typer.Option(False, "--json"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Julga a qualidade das respostas (0..1) contra o que era esperado."""

    runtime = get_runtime(workspace)
    suite = _suite(runtime, suite_id)
    report = runtime.evaluator.judge(suite, method=method, actor="cli")
    if as_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return
    metrics = report["métricas"]
    kv(
        f"Qualidade — {suite_id}",
        {
            "execução": report["run"],
            "método": metrics["método"],
            "casos avaliados": metrics["casos"],
            "qualidade média": round(metrics["qualidade_média"], 3),
            "pior nota": metrics["mínima"],
            "melhor nota": metrics["máxima"],
            "limite": report["limite"] if report["limite"] is not None else "-",
            "degradados": metrics["degradados"],
            "custo do juiz": round(metrics["custo"], 6),
        },
    )
    if report["notas"]:
        table(
            "Notas",
            ["caso", "método", "nota", "motivo"],
            [[item["caso"], item["método"], item["nota"], item["motivo"][:60]] for item in report["notas"]],
        )
    if report["reprovado"] == "sim":
        warning(f"qualidade abaixo do mínimo ({report['limite']})")
        raise typer.Exit(code=1)
    success("qualidade dentro do limite")


@app.command(name="compare")
def compare(
    suite_id: str = typer.Argument(..., help="id da suíte"),
    models: str = typer.Option(..., "--models", "-m", help="provedores separados por vírgula"),
    as_json: bool = typer.Option(False, "--json"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Mesma suíte em provedores diferentes: quem entrega mais por menos."""

    runtime = get_runtime(workspace)
    suite = _suite(runtime, suite_id)
    providers = [item.strip() for item in (models or "").split(",") if item.strip()]
    if not providers:
        error("informe ao menos um provedor em --models")
        raise typer.Exit(code=2)
    report = runtime.evaluator.compare(suite, providers, actor="cli")
    if as_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return
    table(
        f"Comparação — {suite_id}",
        ["provedor", "executou", "acerto", "qualidade", "custo", "p95 ms", "veredito"],
        [
            [
                row["provedor"],
                "sim" if row["executou"] else "não",
                f"{row['taxa_de_acerto']:.0%}",
                row["qualidade"],
                round(row["custo"], 6),
                row["p95_ms"] if row["p95_ms"] is not None else "-",
                row["veredito"] if not row["erro"] else row["erro"][:24],
            ]
            for row in report["provedores"]
        ],
    )
    for note in report["observações"]:
        warning(note)
    if report["melhor"]:
        success(f"melhor colocação: {report['melhor']}")


@app.command(name="load")
def load(
    suite_id: str = typer.Argument(..., help="id da suíte"),
    requests: int = typer.Option(10, "--requests", "-n", help="quantidade de requisições"),
    concurrency: int = typer.Option(2, "--concurrency", "-c", help="requisições simultâneas"),
    as_json: bool = typer.Option(False, "--json"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Simula carga: a suíte repetida, sob as mesmas regras e o mesmo orçamento."""

    runtime = get_runtime(workspace)
    suite = _suite(runtime, suite_id)
    result = runtime.evaluator.load(suite, requests=requests, concurrency=concurrency, actor="cli")
    if as_json:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))
        return
    metrics = result.metrics
    kv(
        f"Carga — {suite_id}",
        {
            "requisições": metrics["requests"],
            "concorrência": metrics["concurrency"],
            "por segundo": metrics["requests_per_second"],
            "p50 / p95 / p99 (ms)": (
                f"{metrics['p50_duration_ms']} / {metrics['p95_duration_ms']} / {metrics['p99_duration_ms']}"
            ),
            "erros": f"{metrics['errors']} ({metrics['error_rate']:.1%})",
            "recusadas pelo orçamento": metrics["budget_denials"],
            "custo total": round(metrics["total_cost"], 6),
            "custo por requisição": round(metrics["cost_per_request"], 8),
            "situação": result.status,
        },
    )
    for reason in result.reasons:
        warning(reason)
    if result.status == "failed":
        raise typer.Exit(code=1)
    success(f"carga {result.status} ({metrics['requests_per_second']} req/s)")


@app.command(name="loads")
def loads(
    suite_id: str = typer.Option(None, "--suite", "-s"),
    limit: int = typer.Option(10, "--limit", "-l"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Histórico das simulações de carga."""

    runtime = get_runtime(workspace)
    rows = runtime.evaluation_loads.list(suite_id=suite_id, limit=limit)
    if not rows:
        info("nenhuma simulação de carga registrada")
        return
    table(
        "Cargas",
        ["quando", "alvo", "requisições", "concorrência", "req/s", "p95 ms", "erros", "situação"],
        [
            [
                item.created_at.strftime("%d/%m %H:%M:%S"),
                f"{item.target_kind}:{item.target}",
                item.requests,
                item.concurrency,
                item.metrics.get("requests_per_second"),
                item.metrics.get("p95_duration_ms"),
                item.metrics.get("errors"),
                item.status,
            ]
            for item in rows
        ],
    )


__all__ = ["app"]
