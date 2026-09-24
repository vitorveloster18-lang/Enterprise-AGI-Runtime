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


class ReleaseApproval(BaseModel):
    """Lacuna 9b: um voto na promoção — com nome, papéis e hora do voto.

    Quórum é gente diferente concordando, não um botão apertado duas vezes. Por
    isso o voto guarda o ator (e seus papéis na hora) e a decisão recusada vale
    como veto: default deny também na promoção.
    """

    release_id: str
    actor: str
    decision: str = "approved"     # approved | rejected
    roles: list[str] = Field(default_factory=list)
    note: str = ""
    at: datetime = Field(default_factory=utcnow)

    @property
    def approved(self) -> bool:
        return self.decision == "approved"

    def summary(self) -> dict[str, Any]:
        return {
            "ator": self.actor,
            "decisão": self.decision,
            "papéis": ", ".join(sorted(self.roles)) or "-",
            "nota": self.note or "-",
            "quando": self.at.isoformat(),
        }


class ReleaseSignature(BaseModel):
    """Lacuna 9b: o que foi assinado, por qual chave e com qual impressão.

    O segredo não viaja: `key_id` identifica a chave mestra do workspace, o
    valor é HMAC-SHA256 do manifesto canônico. Assinatura é sobre **conteúdo** —
    mudou um item, mudou a versão, mudou a evidência: a assinatura não serve
    mais, e verificar diz isso.
    """

    release_id: str
    manifest_hash: str
    algorithm: str = "hmac-sha256"
    key_id: str = ""
    value: str = ""
    signed_by: str = ""
    signed_at: datetime = Field(default_factory=utcnow)

    def summary(self) -> dict[str, Any]:
        return {
            "release": self.release_id,
            "algoritmo": self.algorithm,
            "chave": self.key_id or "-",
            "impressão": self.manifest_hash[:16],
            "assinado_por": self.signed_by or "-",
            "quando": self.signed_at.isoformat(),
        }


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
    #: lacuna 9b: votos da promoção (quórum) e assinatura do manifesto
    approvals: list[ReleaseApproval] = Field(default_factory=list)
    signature: ReleaseSignature | None = None

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

    # ---- lacuna 9b ----------------------------------------------------
    @property
    def approvals_made(self) -> list[ReleaseApproval]:
        return [item for item in self.approvals if item.approved]

    @property
    def vetoes(self) -> list[ReleaseApproval]:
        return [item for item in self.approvals if not item.approved]

    def approvers(self, *, exclude_author: bool = True, author: str = "") -> list[str]:
        excluded = (author or self.created_by).replace("human:", "")
        names = []
        for item in self.approvals_made:
            name = item.actor.replace("human:", "")
            if exclude_author and name == excluded:
                continue
            if name not in names:
                names.append(name)
        return names

    @property
    def signed(self) -> bool:
        return self.signature is not None and bool(self.signature.value)

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
            # lacuna 9b
            "assinado": self.signed,
            "aprovadores": self.approvers(),
            "vetos": len(self.vetoes),
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


__all__ = [
    "LADDER",
    "ArtifactVersion",
    "Release",
    "ReleaseApproval",
    "ReleaseItem",
    "ReleaseSignature",
    "ReleaseStatus",
    "rank",
]
