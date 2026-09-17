"""Evaluation — provar que o trabalho é bom, não apenas que é seguro (Fase 8).

A Fase 7 garante que o artefato entra sem abrir uma porta. A Fase 8 garante que
ele **continua bom**: casos executados, métricas (acerto, custo, latência),
comparação contra uma baseline e uma varredura de segurança do próprio alvo.

A avaliação é um objeto auditado: `EvaluationRun` guarda o que foi medido, qual
limite foi desrespeitado e contra qual baseline — para que "está pior" seja uma
afirmação com números, não uma opinião.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import Environment, EvaluationStatus, EvaluationTarget, FindingSeverity


class Thresholds(BaseModel):
    """O mínimo aceitável. Sem limite declarado, o limite é o da empresa por omissão."""

    min_pass_rate: float = 1.0
    max_total_cost: float | None = None
    max_p95_duration_ms: int | None = None
    max_regressions: int = 0
    #: latência pode piorar até este percentual sem contar como regressão
    max_latency_drift_pct: float = 25.0


class EvaluationCase(BaseModel):
    """Um caso: o que chamar e o que esperar."""

    id: str
    name: str = ""
    description: str = ""
    args: dict = Field(default_factory=dict)
    #: expressões seguras avaliadas contra o contexto do resultado
    expect: list[str] = Field(default_factory=list)
    expect_ok: bool | None = None
    max_cost: float | None = None
    max_duration_ms: int | None = None
    tags: list[str] = Field(default_factory=list)


class EvaluationSuite(BaseModel):
    id: str
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    target_kind: EvaluationTarget = EvaluationTarget.TOOL
    target: str = ""
    environment: Environment = Environment.DEVELOPMENT
    #: ferramentas e workflows rodam isolados; dry_run desligado = efeito real
    dry_run: bool = True
    cases: list[EvaluationCase] = Field(default_factory=list)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    metadata: dict = Field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        payload = "|".join(
            f"{case.id}:{case.expect_ok}:{','.join(case.expect)}" for case in self.cases
        )
        return hashlib.sha256(f"{self.target_kind}:{self.target}:{payload}".encode()).hexdigest()[:12]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "target_kind": str(self.target_kind),
            "target": self.target,
            "cases": len(self.cases),
            "environment": str(self.environment),
            "dry_run": self.dry_run,
            "min_pass_rate": self.thresholds.min_pass_rate,
            "fingerprint": self.fingerprint,
        }


class CheckResult(BaseModel):
    expression: str
    ok: bool
    detail: str = ""


class CaseResult(BaseModel):
    case_id: str
    name: str = ""
    ok: bool = False
    error: str | None = None
    output: Any = None
    duration_ms: int = 0
    cost: float = 0.0
    checks: list[CheckResult] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.ok]


class RegressionReport(BaseModel):
    baseline_run: str
    new_failures: list[str] = Field(default_factory=list)
    fixed: list[str] = Field(default_factory=list)
    pass_rate_delta: float = 0.0
    cost_delta: float = 0.0
    latency_drift_pct: float = 0.0
    regressions: int = 0
    notes: list[str] = Field(default_factory=list)


class SecurityFinding(BaseModel):
    code: str
    severity: FindingSeverity = FindingSeverity.WARNING
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == FindingSeverity.CRITICAL


class EvaluationRun(BaseModel):
    id: str
    suite_id: str
    suite_version: str = "1.0.0"
    suite_fingerprint: str = ""
    target_kind: EvaluationTarget = EvaluationTarget.TOOL
    target: str = ""
    environment: Environment = Environment.DEVELOPMENT
    status: EvaluationStatus = EvaluationStatus.PASSED
    cases: list[CaseResult] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    baseline_run: str | None = None
    comparison: RegressionReport | None = None
    findings: list[SecurityFinding] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    artifact_version: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    duration_ms: int = 0
    created_by: str = "cli"

    # ---- leitura ------------------------------------------------------
    @property
    def passed_cases(self) -> list[CaseResult]:
        return [case for case in self.cases if case.ok]

    @property
    def pass_rate(self) -> float:
        return float(self.metrics.get("pass_rate", 0.0))

    @property
    def blocking_findings(self) -> list[SecurityFinding]:
        return [finding for finding in self.findings if finding.blocking]

    @property
    def acceptable(self) -> bool:
        return self.status == EvaluationStatus.PASSED

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "suite": self.suite_id,
            "target": f"{self.target_kind}:{self.target}",
            "status": str(self.status),
            "cases": f"{len(self.passed_cases)}/{len(self.cases)}",
            "pass_rate": round(self.pass_rate, 3),
            "cost": round(float(self.metrics.get("total_cost", 0.0)), 6),
            "p95_ms": self.metrics.get("p95_duration_ms"),
            "regressions": (self.comparison.regressions if self.comparison else 0),
            "findings": len(self.findings),
            "blocking_findings": len(self.blocking_findings),
            "baseline": self.baseline_run,
            "reasons": self.reasons,
            "created_at": self.created_at.isoformat(),
        }


__all__ = [
    "CaseResult",
    "CheckResult",
    "EvaluationCase",
    "EvaluationRun",
    "EvaluationStatus",
    "EvaluationSuite",
    "EvaluationTarget",
    "FindingSeverity",
    "RegressionReport",
    "SecurityFinding",
    "Thresholds",
]
