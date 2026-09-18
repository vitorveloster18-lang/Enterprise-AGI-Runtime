"""Memory belongs to the enterprise: knowledge, operational, episodic, semantic."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import MemoryKind


class MemoryRecord(BaseModel):
    id: str
    namespace: str = "default"
    kind: MemoryKind = MemoryKind.OPERATIONAL
    content: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = "runtime"
    task_id: str | None = None
    agent_id: str | None = None
    metadata: dict = Field(default_factory=dict)
    #: lacuna 5b: memória multimodal — texto | imagem | áudio | documento
    modality: str = "texto"
    asset: MediaAsset | None = None
    score: float | None = None  # relevance, filled on search
    created_at: datetime = Field(default_factory=utcnow)

    # ---- ciclo de vida (Fase 5) ---------------------------------------
    #: 0..1 — quanto esta memória vale por natureza (ver memory/salience.py)
    importance: float = 0.4
    #: quantas vezes foi recuperada (reforço)
    access_count: int = 0
    last_accessed_at: datetime | None = None
    #: arquivada não aparece na busca padrão, mas não é apagada
    archived: bool = False
    #: id da memória que sobreviveu à consolidação (near-duplicata)
    duplicate_of: str | None = None
    #: modelo que gerou o vetor — habilita reindexação ao trocar de backend
    embedding_model: str | None = None

    @property
    def media(self) -> bool:
        """Tem binário associado (o conteúdo é a legenda, não o arquivo)."""

        return self.asset is not None

    @property
    def age_days(self) -> float:
        from datetime import datetime

        reference = self.last_accessed_at or self.created_at
        if isinstance(reference, str):
            reference = datetime.fromisoformat(reference)
        return max(0.0, (utcnow() - reference).total_seconds() / 86400.0)


class MediaAsset(BaseModel):
    """Lacuna 5b: a referência ao binário — nunca o binário na memória.

    O arquivo vive em `artifacts/media/`; aqui ficam o caminho relativo, o tipo
    conferido pelos bytes, o tamanho, a impressão digital e a **legenda**, que é
    a única parte que vira texto buscável (e que entra no contexto do modelo).
    """

    id: str
    namespace: str = "default"
    filename: str = ""
    mime: str = ""
    size: int = 0
    fingerprint: str = ""
    modality: str = "arquivo"      # imagem | áudio | documento | texto
    path: str = ""                 # relativo ao workspace
    caption: str = ""              # descrição declarada (o lado textual)
    created_by: str = "cli"
    created_at: datetime = Field(default_factory=utcnow)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "arquivo": self.filename,
            "tipo": self.mime,
            "modalidade": self.modality,
            "tamanho": self.size,
            "impressão": self.fingerprint[:16],
            "caminho": self.path,
            "legenda": self.caption or "[sem legenda]",
        }


class MemoryQuery(BaseModel):
    query: str = ""
    namespaces: list[str] = Field(default_factory=list)
    kinds: list[MemoryKind] = Field(default_factory=list)
    limit: int = 5
