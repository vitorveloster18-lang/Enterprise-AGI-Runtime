"""Lacuna 8b: carga — quantas requisições o Runtime aguenta, e a que preço.

Não é teste de "performance" pela performance: é **governança sob pressão**.
Cada requisição da simulação passa pelo mesmo caminho de sempre — política,
orçamento, aprovação se for o caso, auditoria. Por isso o relatório separa o que
falhou por **erro** daquilo que o **orçamento recusou**: estourar o teto não é
lentidão, é o Runtime fazendo o que prometeu.

A simulação roda em um pool de threads (o banco do Runtime já é thread-safe) e
termina sempre: cada requisição tem tempo limite e o relatório devolve p50, p95,
p99, vazão e custo por requisição.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.enums import EventType
from ..domain.evaluation import EvaluationSuite, LoadRun
from .metrics import percentile

BUDGET_DENIAL_MARKERS = ("orçamento", "budget", "excedido", "exceeded")


class LoadRunner:
    """Repete a execução de uma suíte e mede o que acontece quando insiste."""

    def __init__(self, runtime: Any, suite: EvaluationSuite, *, timeout: int | None = None):
        self.runtime = runtime
        self.suite = suite
        self.timeout = timeout

    # ---- uma requisição ----------------------------------------------
    def once(self, case=None):
        """Executa um caso do mesmo jeito que a suíte executa — sem atalho."""

        case = case or self.suite.cases[0]
        started = time.perf_counter()
        try:
            result = self.runtime.evaluator._run_case(self.suite, case, timeout=self.timeout or 60)
            elapsed = int((time.perf_counter() - started) * 1000)
            return {
                "ok": bool(result.ok),
                "duration_ms": elapsed,
                "cost": float(result.cost or 0.0),
                "error": (result.error or "")[:200] if not result.ok else "",
                "budget_denied": _is_budget_denial(result.error or ""),
            }
        except Exception as exc:  # falha de infraestrutura também é medição
            elapsed = int((time.perf_counter() - started) * 1000)
            message = f"{type(exc).__name__}: {exc}"
            return {
                "ok": False,
                "duration_ms": elapsed,
                "cost": 0.0,
                "error": message[:200],
                "budget_denied": _is_budget_denial(message),
            }

    # ---- a simulação -------------------------------------------------
    def run(
        self,
        *,
        requests: int = 10,
        concurrency: int = 2,
        actor: str = "cli",
    ) -> LoadRun:
        if not self.suite.cases:
            raise ValueError(f"suíte '{self.suite.id}' não tem casos")

        load = LoadRun(
            id=new_id("load"),
            suite_id=self.suite.id,
            target_kind=str(self.suite.target_kind),
            target=self.suite.target,
            requests=max(1, int(requests)),
            concurrency=max(1, int(concurrency)),
            created_by=actor,
        )
        started = time.perf_counter()
        wall_start = utcnow()

        cases = [self.suite.cases[index % len(self.suite.cases)] for index in range(load.requests)]
        samples: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=load.concurrency) as pool:
            for sample in pool.map(self.once, cases):
                samples.append(sample)

        durations = sorted(sample["duration_ms"] for sample in samples)
        errors = [sample for sample in samples if not sample["ok"]]
        denied = [sample for sample in errors if sample["budget_denied"]]
        total_cost = round(sum(sample["cost"] for sample in samples), 8)
        elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))

        load.metrics = {
            "requests": len(samples),
            "ok": len(samples) - len(errors),
            "errors": len(errors),
            "error_rate": round(len(errors) / len(samples), 4) if samples else 0.0,
            "budget_denials": len(denied),
            "p50_duration_ms": percentile(durations, 50),
            "p95_duration_ms": percentile(durations, 95),
            "p99_duration_ms": percentile(durations, 99),
            "max_duration_ms": percentile(durations, 100),
            "avg_duration_ms": round(sum(durations) / len(durations)) if durations else 0,
            "total_cost": total_cost,
            "cost_per_request": round(total_cost / len(samples), 8) if samples else 0.0,
            "requests_per_second": round(len(samples) / (elapsed_ms / 1000), 3) if elapsed_ms else 0.0,
            "concurrency": load.concurrency,
        }
        load.duration_ms = elapsed_ms
        load.finished_at = utcnow()
        load.status, load.reasons = self._verdict(load, wall_start)
        return load

    # ---- veredito ----------------------------------------------------
    def _verdict(self, load: LoadRun, started: datetime) -> tuple[str, list[str]]:
        thresholds = self.suite.thresholds
        reasons: list[str] = []

        if load.error_rate > 0.05:
            reasons.append(
                f"taxa de erro {load.error_rate:.1%} acima de 5% "
                f"({load.metrics['errors']} de {load.metrics['requests']})"
            )
        if load.metrics.get("budget_denials"):
            reasons.append(
                f"{load.metrics['budget_denials']} requisição(ões) recusadas pelo orçamento "
                "(o teto está sendo atingido antes da capacidade)"
            )
        max_p95 = thresholds.max_p95_duration_ms
        if max_p95 is not None and load.metrics.get("p95_duration_ms", 0) > max_p95:
            reasons.append(f"p95 {load.metrics['p95_duration_ms']} ms acima do teto de {max_p95} ms")

        status = "failed" if reasons else "passed"
        if status == "failed" and not load.metrics.get("budget_denials") and load.error_rate <= 0.5:
            status = "degraded"
        return status, reasons


def _is_budget_denial(message: str) -> bool:
    low = (message or "").lower()
    return any(marker in low for marker in BUDGET_DENIAL_MARKERS)


def record(runtime: Any, load: LoadRun, *, actor: str = "cli") -> LoadRun:
    """Carga também é trilha: o que foi medido fica registrado."""

    runtime.evaluation_loads.save(load)
    runtime.audit.record(
        EventType.EVAL_LOAD_FINISHED,
        actor=actor,
        environment=str(runtime.settings.environment),
        payload={
            "carga": load.id,
            "suíte": load.suite_id,
            "alvo": f"{load.target_kind}:{load.target}",
            "requisições": load.requests,
            "concorrência": load.concurrency,
            "p95_ms": load.metrics.get("p95_duration_ms"),
            "erros": load.metrics.get("errors"),
            "orçamento_recusou": load.metrics.get("budget_denials"),
            "situação": load.status,
        },
    )
    return load


__all__ = ["BUDGET_DENIAL_MARKERS", "LoadRunner", "record"]
