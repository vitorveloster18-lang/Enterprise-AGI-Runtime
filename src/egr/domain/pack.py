"""Pacotes verticais (Fase 12): a empresa não começa do zero.

Um **pack** é um conjunto declarado de agentes, workflows, políticas,
conectores, suítes de avaliação e documentos de um vertical (Financeiro,
Contábil, Vendas, Operações, RH, Marketing, Suporte).

Ele não é um instalador cego: o pack é uma **proposta** (Fase 7). Passa por
verificação (requisitos e colisões), precisa de aprovação humana e só então é
aplicado pelo único escritor do workspace. Conteúdo de pack é conteúdo de
empresa — entra pelo mesmo portão que o resto.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import PackStatus

ARTIFACT_KINDS = ("agents", "workflows", "policies", "integrations", "evaluations")
#: para cada tipo de artefato, o diretório do workspace onde ele mora
ARTIFACT_DIRS = {
    "agents": "agents",
    "workflows": "workflows",
    "policies": "policies",
    "integrations": "integrations",
    "evaluations": "evaluations",
}


class PackRequirements(BaseModel):
    """O que precisa existir antes. Sem isso, o pack não é nem proposto."""

    tools: list[str] = Field(default_factory=list)
    connectors: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    #: ambiente mínimo em que a instalação é permitida
    min_environment: str = "development"


class Pack(BaseModel):
    """Um vertical declarado em um arquivo só — revisável em revisão de código."""

    id: str
    name: str = ""
    version: str = "1.0.0"
    vertical: str = ""
    description: str = ""
    requires: PackRequirements = Field(default_factory=PackRequirements)
    agents: list[dict] = Field(default_factory=list)
    workflows: list[dict] = Field(default_factory=list)
    policies: list[dict] = Field(default_factory=list)
    integrations: list[dict] = Field(default_factory=list)
    evaluations: list[dict] = Field(default_factory=list)
    documents: dict[str, str] = Field(default_factory=dict)
    #: origem do arquivo (builtin | workspace) — preenchido pelo catálogo
    origin: str = "builtin"

    @property
    def title(self) -> str:
        return self.name or self.id

    @property
    def counts(self) -> dict[str, int]:
        return {
            "agentes": len(self.agents),
            "workflows": len(self.workflows),
            "políticas": len(self.policies),
            "conectores": len(self.integrations),
            "avaliações": len(self.evaluations),
            "documentos": len(self.documents),
        }

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def artifacts(self) -> dict[str, list[dict]]:
        return {kind: list(getattr(self, kind)) for kind in ARTIFACT_KINDS}

    def checksum(self) -> str:
        """Impressão digital do conteúdo: versão instalada é conteúdo, não nome."""

        payload = self.model_dump(mode="json", exclude={"origin"})
        return hashlib.sha256(
            __import__("json").dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]

    def to_yaml(self) -> str:
        import yaml

        payload = self.model_dump(mode="json", exclude={"origin"}, exclude_none=True)
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default_flow_style=False)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nome": self.title,
            "versão": self.version,
            "vertical": self.vertical or "-",
            "origem": self.origin,
            "artefatos": self.total,
            **self.counts,
            "requisitos": ", ".join(
                [*self.requires.tools, *self.requires.connectors, *self.requires.agents]
            )
            or "-",
            "descrição": self.description or "-",
        }


class InstalledPack(BaseModel):
    """O que este workspace tem instalado, por quem e com qual conteúdo."""

    id: str
    version: str = "1.0.0"
    status: PackStatus = PackStatus.INSTALLED
    checksum: str = ""
    source: str = "builtin"
    files: list[str] = Field(default_factory=list)
    #: impressão digital de cada arquivo escrito: só removemos o que não mudou
    checksums: dict[str, str] = Field(default_factory=dict)
    installed_by: str = "human:cli"
    proposal: str | None = None
    installed_at: datetime | None = Field(default_factory=utcnow)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "versão": self.version,
            "status": str(self.status),
            "impressão": self.checksum or "-",
            "arquivos": len(self.files),
            "instalado por": self.installed_by,
            "proposta": self.proposal or "-",
            "quando": self.installed_at.isoformat() if self.installed_at else "-",
        }


__all__ = [
    "ARTIFACT_DIRS",
    "ARTIFACT_KINDS",
    "InstalledPack",
    "Pack",
    "PackRequirements",
]
