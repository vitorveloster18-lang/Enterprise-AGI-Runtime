"""Change Proposal — como o Runtime cresce sem perder governo (Fase 7).

Um agente pode *propor* um agente, uma ferramenta, um workflow ou uma política.
Ele nunca pode *aplicar*: proposta entra como rascunho, passa por verificação
estática, executa em sandbox quando é código, e só então um humano aprova.

A proposta é um objeto auditado com linhagem: o artefato aplicado carrega
quem o criou e de qual proposta veio.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import Environment, ProposalKind, ProposalStatus, RiskLevel


class ValidationCheck(BaseModel):
    """Uma verificação estática sobre o conteúdo proposto."""

    name: str
    ok: bool = True
    level: str = "error"  # error | warning | info
    detail: str = ""


class TrialReport(BaseModel):
    """Resultado de executar a proposta dentro do sandbox."""

    ok: bool = False
    mode: str = "process"  # process | container
    duration_ms: int = 0
    exit_code: int = 0
    output: Any = None
    error: str | None = None
    cost: float = 0.0
    stdout: str = ""
    stderr: str = ""
    files: list[str] = Field(default_factory=list)  # arquivos criados no sandbox
    args: dict = Field(default_factory=dict)
    timed_out: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class ChangeProposal(BaseModel):
    id: str
    kind: ProposalKind
    name: str  # id do artefato: agent id, tool name, workflow id, policy id
    target: str  # caminho relativo no workspace (agents/x.yaml, tools/x.py)
    content: str = ""
    origin: str = "human:cli"  # "agent:<id>" | "human:<ator>"
    rationale: str = ""
    environment: Environment = Environment.DEVELOPMENT
    status: ProposalStatus = ProposalStatus.DRAFT
    risk: RiskLevel = RiskLevel.LOW
    requires_approval: bool = True
    fingerprint: str = ""
    checks: list[ValidationCheck] = Field(default_factory=list)
    trials: list[TrialReport] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_note: str | None = None
    applied_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)

    # ---- conteúdo -----------------------------------------------------
    @staticmethod
    def fingerprint_of(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def refresh_fingerprint(self) -> None:
        self.fingerprint = self.fingerprint_of(self.content)

    @property
    def content_matches_fingerprint(self) -> bool:
        return bool(self.fingerprint) and self.fingerprint == self.fingerprint_of(self.content)

    # ---- verificações -------------------------------------------------
    @property
    def errors(self) -> list[ValidationCheck]:
        return [check for check in self.checks if not check.ok and check.level == "error"]

    @property
    def warnings(self) -> list[ValidationCheck]:
        return [check for check in self.checks if not check.ok and check.level == "warning"]

    @property
    def valid(self) -> bool:
        return bool(self.checks) and not self.errors

    @property
    def last_trial(self) -> TrialReport | None:
        return self.trials[-1] if self.trials else None

    @property
    def tested_ok(self) -> bool:
        trial = self.last_trial
        return bool(trial and trial.ok)

    @property
    def created_by_agent(self) -> str | None:
        return self.origin.split(":", 1)[1] if self.origin.startswith("agent:") else None

    @property
    def open(self) -> bool:
        return self.status not in (ProposalStatus.APPLIED, ProposalStatus.REJECTED)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": str(self.kind),
            "name": self.name,
            "target": self.target,
            "status": str(self.status),
            "origin": self.origin,
            "risk": str(self.risk),
            "environment": str(self.environment),
            "checks": len(self.checks),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "trials": len(self.trials),
            "last_trial_ok": self.tested_ok if self.trials else None,
            "requires_approval": self.requires_approval,
            "created_at": self.created_at.isoformat(),
        }
