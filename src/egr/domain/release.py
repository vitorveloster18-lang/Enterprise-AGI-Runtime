"""Release — promoção entre ambientes como objeto auditado (Fase 9).

    development → staging → produção, sempre com proposta e humano no meio

Um artefato não "vai para produção": ele entra em um **release** que carrega
itens versionados, evidência (avaliação aprovada, varredura limpa), os gates que
foram conferidos, quem aprovou e o que fazer se precisar voltar.

Versões são snapshots de conteúdo (`ArtifactVersion`): rollback é restaurar um
snapshot — não "lembrar como era antes".
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import Environment, ReleaseStatus
from .proposal import ValidationCheck  # a mesma noção de verificação da Fase 7

#: ordem dos ambientes: ninguém pula degrau
LADDER: dict[str, int] = {
    Environment.DEVELOPMENT: 0,
    Environment.STAGING: 1,
    Environment.PRODUCTION: 2,
}


def rank(environment: Environment | str) -> int:
    return LADDER.get(Environment(str(environment)), 0)


class ReleaseItem(BaseModel):
    """Um artefato promovido, com a versão exata que foi para o ambiente."""

    kind: str  # agent | tool | workflow | policy
    name: str
    version: str = ""
    revision: int = 0
    fingerprint: str = ""
    from_environment: Environment = Environment.DEVELOPMENT
    to_environment: Environment = Environment.STAGING

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


class Release(BaseModel):
    id: str
    title: str = ""
    reason: str = ""
    items: list[ReleaseItem] = Field(default_factory=list)
    target: Environment = Environment.STAGING
    status: ReleaseStatus = ReleaseStatus.DRAFT
    checks: list[ValidationCheck] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # ids de execuções de avaliação
    created_by: str = "cli"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_note: str | None = None
    deployed_at: datetime | None = None
    rolled_back_at: datetime | None = None
    rollback_of: str | None = None
    metadata: dict = Field(default_factory=dict)

    # ---- leitura ------------------------------------------------------
    @property
    def errors(self) -> list[ValidationCheck]:
        return [check for check in self.checks if not check.ok and check.level == "error"]

    @property
    def warnings(self) -> list[ValidationCheck]:
        return [check for check in self.checks if not check.ok and check.level == "warning"]

    @property
    def clear(self) -> bool:
        return bool(self.checks) and not self.errors

    @property
    def open(self) -> bool:
        return self.status not in (
            ReleaseStatus.DEPLOYED,
            ReleaseStatus.REJECTED,
            ReleaseStatus.ROLLED_BACK,
        )

    @property
    def production(self) -> bool:
        return Environment(str(self.target)) == Environment.PRODUCTION

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "target": str(self.target),
            "status": str(self.status),
            "itens": [item.key for item in self.items],
            "versões": [f"{item.name}@{item.version or 'r' + str(item.revision)}" for item in self.items],
            "gates": f"{len(self.checks) - len(self.errors)}/{len(self.checks)}",
            "erros": len(self.errors),
            "avisos": len(self.warnings),
            "evidência": self.evidence,
            "criado_por": self.created_by,
            "decidido_por": self.decided_by,
            "rollback_de": self.rollback_of,
            "criado_em": self.created_at.isoformat(),
        }


class ArtifactVersion(BaseModel):
    """Snapshot do conteúdo de um artefato — a matéria-prima do rollback."""

    id: str  # <kind>:<name>@<revision>
    kind: str
    name: str
    revision: int
    version: str = ""
    content: str = ""
    fingerprint: str = ""
    environment: Environment = Environment.DEVELOPMENT
    created_by: str = "cli"
    created_at: datetime = Field(default_factory=utcnow)
    release_id: str | None = None
    note: str = ""

    @staticmethod
    def fingerprint_of(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def refresh_fingerprint(self) -> None:
        self.fingerprint = self.fingerprint_of(self.content)

    @property
    def label(self) -> str:
        return f"{self.name}@{self.version or 'r' + str(self.revision)}"


__all__ = ["LADDER", "ArtifactVersion", "Release", "ReleaseItem", "ReleaseStatus", "rank"]
