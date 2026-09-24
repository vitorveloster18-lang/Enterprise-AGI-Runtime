"""O que merece ser lembrado: importância, reforço, decaimento e arquivamento.

Memória que só cresce vira lixo com boa indexação. Este módulo dá ao Runtime
uma política explícita de ciclo de vida:

    importância (atribuída na escrita)
        × reforço  (1 + log(1 + acessos))     — lembrada com frequência vale mais
        × decaimento (0.5 ^ (idade / meia-vida)) — o que ninguém usa esfria

A saliência resultante é o que a consolidação usa para escolher **qual das
duas quase-duplicatas sobrevive** e o que pode ser arquivado. Nada é apagado
silenciosamente: duplicatas vão para `archived` e só o `prune` — explícito e
auditado — remove de vez.
"""

from __future__ import annotations

import math

from ..core.timeutil import utcnow
from ..domain.enums import MemoryKind
from ..domain.memory import MemoryRecord

#: quanto cada tipo de memória vale por padrão (conhecimento > episódio solto)
BASE_IMPORTANCE: dict[str, float] = {
    MemoryKind.KNOWLEDGE: 0.80,
    MemoryKind.SEMANTIC: 0.60,
    MemoryKind.OPERATIONAL: 0.45,
    MemoryKind.EPISODIC: 0.30,
}

DEFAULT_IMPORTANCE = 0.40

#: sinais de que o conteúdo é norma/decisão, não ruído de execução
DECISION_MARKERS = (
    "politica",
    "política",
    "regra",
    "limite",
    "prazo",
    "aprovacao",
    "aprovação",
    "nunca",
    "sempre",
    "obrigat",
    "proibido",
    "padrao",
    "padrão",
)


def base_importance(kind: MemoryKind | str) -> float:
    return BASE_IMPORTANCE.get(str(kind), DEFAULT_IMPORTANCE)


def infer_importance(
    content: str,
    *,
    kind: MemoryKind | str = MemoryKind.OPERATIONAL,
    tags: list[str] | None = None,
) -> float:
    """Heurística simples e auditável: tipo + sinais de norma/decisão."""

    importance = base_importance(kind)
    haystack = f"{content} {' '.join(tags or [])}".lower()
    if any(marker in haystack for marker in DECISION_MARKERS):
        importance = min(1.0, importance + 0.15)
    if len(content) > 800:  # conteúdo longo tende a ser documento, não ruído
        importance = min(1.0, importance + 0.05)
    return round(importance, 4)


def salience(record: MemoryRecord, *, half_life_days: int = 30, now=None) -> float:
    """Importância ajustada por uso e pelo tempo. Quanto maior, mais viva."""

    if half_life_days <= 0:
        half_life_days = 1
    moment = now or utcnow()
    reference = record.last_accessed_at or record.created_at
    age_days = max(0.0, (moment - reference).total_seconds() / 86400.0)
    decay = 0.5 ** (age_days / half_life_days)
    reinforcement = 1.0 + math.log1p(max(0, record.access_count))
    return round(record.importance * decay * reinforcement, 6)


def reinforce(record: MemoryRecord, *, now=None) -> MemoryRecord:
    """Chamada a cada recall: memória usada fica mais forte e mais recente."""

    record.access_count = int(record.access_count or 0) + 1
    record.last_accessed_at = now or utcnow()
    return record


def classify(score: float) -> str:
    if score >= 0.60:
        return "viva"
    if score >= 0.30:
        return "estável"
    if score >= 0.12:
        return "esfriando"
    return "arquivável"


__all__ = [
    "BASE_IMPORTANCE",
    "base_importance",
    "classify",
    "infer_importance",
    "reinforce",
    "salience",
]
