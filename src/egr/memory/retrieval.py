"""Recuperação híbrida: léxico (BM25/FTS5) + semântico (cosseno), fundidos por RRF.

Por que os dois:

* **BM25** é preciso para termos raros e exatos — "NF 82731", "CNPJ 00.000.000/0001-91".
* **Cosseno** pega variação, erro de digitação e vocabulário diferente —
  "liberar pagamento" encontra "aprovação automática de pagamentos".

A fusão usa **Reciprocal Rank Fusion**: `w_l / (k + rank_l) + w_s / (k + rank_s)`.
RRF não exige normalizar escalas incomparáveis (BM25 é ilimitado e negativo,
cosseno vai de -1 a 1), e é estável quando um dos lados não traz candidato algum.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.memory import MemoryRecord
from .embeddings import cosine, embed

RRF_K = 60


@dataclass
class Candidate:
    record: MemoryRecord
    fts_rank: int | None = None
    fts_score: float | None = None
    semantic_rank: int | None = None
    cosine: float | None = None
    fused: float = 0.0

    def as_trace(self) -> dict:
        return {
            "fts_rank": self.fts_rank,
            "fts_score": self.fts_score,
            "semantic_rank": self.semantic_rank,
            "cosine": self.cosine,
            "fused": round(self.fused, 6),
        }


def fuse(
    lexical: list[tuple[MemoryRecord, float | None]],
    semantic: list[tuple[MemoryRecord, float]],
    *,
    lexical_weight: float = 1.0,
    semantic_weight: float = 1.0,
) -> list[Candidate]:
    """Combina as duas listas ordenadas por RRF ponderado."""

    merged: dict[str, Candidate] = {}

    for position, (record, score) in enumerate(lexical, start=1):
        candidate = merged.setdefault(record.id, Candidate(record=record))
        candidate.fts_rank = position
        candidate.fts_score = score

    for position, (record, similarity) in enumerate(semantic, start=1):
        candidate = merged.setdefault(record.id, Candidate(record=record))
        candidate.semantic_rank = position
        candidate.cosine = similarity

    for candidate in merged.values():
        score = 0.0
        if candidate.fts_rank is not None:
            score += lexical_weight / (RRF_K + candidate.fts_rank)
        if candidate.semantic_rank is not None:
            score += semantic_weight / (RRF_K + candidate.semantic_rank)
        candidate.fused = score

    return sorted(merged.values(), key=lambda item: (-item.fused, item.record.id))


def rank_by_similarity(
    query_vector: list[float],
    vectors: dict[str, list[float]],
    records: dict[str, MemoryRecord],
    *,
    limit: int,
    min_cosine: float,
) -> list[tuple[MemoryRecord, float]]:
    """Ordena por similaridade de cosseno, descartando o que está abaixo do corte."""

    scored: list[tuple[MemoryRecord, float]] = []
    for record_id, vector in vectors.items():
        record = records.get(record_id)
        if record is None:
            continue
        similarity = cosine(query_vector, vector)
        if similarity < min_cosine:
            continue
        scored.append((record, similarity))
    scored.sort(key=lambda item: (-item[1], item[0].id))
    return scored[:limit]


def query_vector(text: str) -> list[float]:
    return embed(text)


__all__ = ["RRF_K", "Candidate", "fuse", "query_vector", "rank_by_similarity"]
