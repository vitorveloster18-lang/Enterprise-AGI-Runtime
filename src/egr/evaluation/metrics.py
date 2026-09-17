"""Métricas e regressão: números que sustentam um veredito."""

from __future__ import annotations

import math
from typing import Any

from ..domain.evaluation import CaseResult, EvaluationRun, RegressionReport, Thresholds


def aggregate(cases: list[CaseResult]) -> dict[str, Any]:
    """Agrega casos em métricas comparáveis entre execuções."""

    total = len(cases)
    passed = sum(1 for case in cases if case.ok)
    durations = sorted(case.duration_ms for case in cases)
    costs = [float(case.cost or 0.0) for case in cases]
    failed = [case for case in cases if not case.ok]

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": (passed / total) if total else 0.0,
        "total_cost": round(sum(costs), 8),
        "avg_cost": round(sum(costs) / total, 8) if total else 0.0,
        "avg_duration_ms": round(sum(durations) / total) if total else 0,
        "p95_duration_ms": percentile(durations, 95) if durations else 0,
        "max_duration_ms": max(durations) if durations else 0,
        "error_rate": round(len(failed) / total, 4) if total else 0.0,
        "failed_cases": [case.case_id for case in failed],
    }


def percentile(values: list[int | float], percent: int) -> int:
    """Percentil simples (menor valor >= p% da amostra)."""

    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
    return int(ordered[min(index, len(ordered) - 1)])


def compare(
    current: EvaluationRun,
    baseline: EvaluationRun,
    thresholds: Thresholds | None = None,
) -> RegressionReport:
    """O que piorou em relação à baseline — e o que melhorou."""

    thresholds = thresholds or current.thresholds
    current_by_id = {case.case_id: case for case in current.cases}
    baseline_by_id = {case.case_id: case for case in baseline.cases}

    new_failures = [
        case_id
        for case_id, case in current_by_id.items()
        if not case.ok and baseline_by_id.get(case_id) is not None and baseline_by_id[case_id].ok
    ]
    fixed = [
        case_id
        for case_id, case in current_by_id.items()
        if case.ok and baseline_by_id.get(case_id) is not None and not baseline_by_id[case_id].ok
    ]

    pass_rate_delta = current.pass_rate - baseline.pass_rate
    cost_delta = float(current.metrics.get("total_cost", 0.0)) - float(baseline.metrics.get("total_cost", 0.0))

    current_avg = current.metrics.get("avg_duration_ms", 0) or 0
    baseline_avg = baseline.metrics.get("avg_duration_ms", 0) or 0
    latency_drift_pct = ((current_avg - baseline_avg) / baseline_avg * 100) if baseline_avg else 0.0

    notes: list[str] = []
    regressions = len(new_failures)
    if new_failures:
        notes.append(f"{len(new_failures)} caso(s) que passavam agora falham: {', '.join(new_failures[:5])}")
    if fixed:
        notes.append(f"{len(fixed)} caso(s) que falhavam agora passam: {', '.join(fixed[:5])}")
    if latency_drift_pct > thresholds.max_latency_drift_pct:
        regressions += 1
        notes.append(
            f"latência média subiu {latency_drift_pct:.0f}% "
            f"({baseline_avg} ms → {current_avg} ms; limite {thresholds.max_latency_drift_pct:.0f}%)"
        )
    if cost_delta > 0:
        notes.append(f"custo subiu {cost_delta:.6f}")
    if pass_rate_delta < 0:
        notes.append(f"taxa de acerto caiu {abs(pass_rate_delta) * 100:.1f} pontos percentuais")

    return RegressionReport(
        baseline_run=baseline.id,
        new_failures=new_failures,
        fixed=fixed,
        pass_rate_delta=round(pass_rate_delta, 4),
        cost_delta=round(cost_delta, 8),
        latency_drift_pct=round(latency_drift_pct, 2),
        regressions=regressions,
        notes=notes,
    )


def verdict(
    run: EvaluationRun,
    *,
    comparison: RegressionReport | None,
    thresholds: Thresholds,
) -> tuple[str, list[str]]:
    """Aplica os limites e devolve (status, motivos)."""

    from ..domain.enums import EvaluationStatus

    reasons: list[str] = []
    status = EvaluationStatus.PASSED

    if run.blocking_findings:
        status = EvaluationStatus.FAILED
        reasons.append(
            f"varredura de segurança encontrou {len(run.blocking_findings)} problema(s) crítico(s): "
            + "; ".join(f"{finding.code}: {finding.detail}" for finding in run.blocking_findings[:3])
        )

    if run.pass_rate < thresholds.min_pass_rate:
        status = EvaluationStatus.FAILED
        reasons.append(
            f"taxa de acerto {run.pass_rate:.0%} abaixo do mínimo {thresholds.min_pass_rate:.0%}"
        )

    if thresholds.max_total_cost is not None and run.metrics.get("total_cost", 0.0) > thresholds.max_total_cost:
        status = EvaluationStatus.FAILED
        reasons.append(f"custo {run.metrics['total_cost']:.6f} acima do teto {thresholds.max_total_cost:.6f}")

    max_p95 = thresholds.max_p95_duration_ms
    if max_p95 is not None and run.metrics.get("p95_duration_ms", 0) > max_p95:
        status = EvaluationStatus.FAILED
        reasons.append(f"p95 {run.metrics['p95_duration_ms']} ms acima do teto {max_p95} ms")

    if comparison and comparison.regressions > thresholds.max_regressions:
        # "piorou em relação à referência" é mais específico do que "reprovou":
        # mantém os motivos de limite e acrescenta o que mudou.
        status = EvaluationStatus.REGRESSED
        reasons.extend(comparison.notes)

    return str(status), reasons


__all__ = ["aggregate", "compare", "percentile", "verdict"]
