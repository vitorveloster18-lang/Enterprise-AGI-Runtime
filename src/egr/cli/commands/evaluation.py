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


__all__ = ["app"]
