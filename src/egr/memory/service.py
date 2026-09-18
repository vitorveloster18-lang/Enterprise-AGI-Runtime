"""Memory service: knowledge / operational / episodic / semantic.

Memory belongs to the enterprise, is namespace-isolated and is always recorded
in the audit trail.

Fase 5:

    escrever -> embedding local + FTS -> memória viva
    recuperar -> BM25 (léxico)  +  cosseno (semântico)  --RRF--> contexto
    usar      -> reforço (acesso conta) e decaimento (tempo esfria)
    consolidar -> near-duplicatas arquivadas, lixo podado mediante --apply

Nada é apagado por decaimento automático: a memória esfria, é arquivada e só
sai do acervo por uma ação explícita (e auditada).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import MemoryConfig
from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.enums import EventType, MemoryKind
from ..domain.memory import MemoryRecord
from ..storage.repositories import MemoryRepository
from . import embeddings
from .media import MediaStore, describe
from .retrieval import fuse, rank_by_similarity
from .salience import classify, infer_importance, reinforce, salience
from .scrubbing import scan as scan_pii
from .scrubbing import scrub as scrub_pii
from .scrubbing import summarize as summarize_pii


class MemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        audit=None,
        default_namespace: str = "default",
        config: MemoryConfig | None = None,
        workspace: Path | None = None,
    ):
        self.repository = repository
        self.audit = audit
        self.default_namespace = default_namespace
        self.config = config or MemoryConfig()
        #: lacuna 5b: o binário fica em artifacts/media, fora do banco
        self.media = MediaStore(workspace or Path.cwd(), config=self.config)

    # ---- write -------------------------------------------------------
    def write(
        self,
        content: str,
        *,
        kind: MemoryKind | str = MemoryKind.OPERATIONAL,
        namespace: str | None = None,
        summary: str = "",
        tags: list[str] | None = None,
        source: str = "runtime",
        task_id: str | None = None,
        agent_id: str | None = None,
        metadata: dict | None = None,
        environment: str | None = None,
        importance: float | None = None,
    ) -> MemoryRecord:
        resolved_kind = MemoryKind(kind) if isinstance(kind, str) else kind
        content, summary_given, removed = self._scrub(content, summary)
        record = MemoryRecord(
            id=new_id("memory"),
            namespace=namespace or self.default_namespace,
            kind=resolved_kind,
            content=content,
            summary=summary_given or content[:280],
            tags=tags or [],
            source=source,
            task_id=task_id,
            agent_id=agent_id,
            metadata=self._metadata_with_pii(metadata, removed),
            created_at=utcnow(),
            importance=(
                importance
                if importance is not None
                else infer_importance(content, kind=resolved_kind, tags=tags)
            ),
        )
        self.repository.save(record)
        self._embed(record)
        if self.audit:
            self.audit.record(
                EventType.MEMORY_WRITTEN,
                actor=agent_id or source,
                task_id=task_id,
                agent_id=agent_id,
                environment=environment or "development",
                payload={
                    "memory_id": record.id,
                    "namespace": record.namespace,
                    "kind": str(record.kind),
                    "summary": record.summary,
                    "chars": len(content),
                    "importance": record.importance,
                    "pii": summarize_pii(removed) or None,
                },
            )
        if removed and self.audit:
            self.audit.record(
                EventType.MEMORY_PII_SCRUBBED,
                actor=agent_id or source,
                environment=environment or "development",
                payload={
                    "memory_id": record.id,
                    "tipos": sorted(removed),
                    "ocorrências": removed,
                },
            )
        return record

    # ---- lacuna 5b: PII ----------------------------------------------
    def _scrub(self, content: str, summary: str = "") -> tuple[str, str, dict[str, int]]:
        """Limpa antes de persistir: o dado pessoal não chega ao banco.

        Devolve (conteúdo limpo, resumo limpo, {tipo: ocorrências}). O que saiu
        é contado por tipo — nunca o valor.
        """

        if not self.config.scrub_pii:
            return content, summary, {}
        allowed = tuple(self.config.pii_allow or ())
        cleaned, removed = scrub_pii(content, allowed=allowed)
        cleaned_summary, removed_summary = scrub_pii(summary, allowed=allowed)
        for name, count in removed_summary.items():
            removed[name] = removed.get(name, 0) + count
        return cleaned, cleaned_summary, removed

    @staticmethod
    def _metadata_with_pii(metadata: dict | None, removed: dict[str, int]) -> dict:
        data = dict(metadata or {})
        if removed:
            data["pii_removido"] = dict(sorted(removed.items()))
        return data

    def scrub(self, text: str) -> tuple[str, dict[str, int]]:
        """Limpeza avulsa (CLI/API): o que sairia de um texto, sem gravar nada."""

        return scrub_pii(text, allowed=tuple(self.config.pii_allow or ()))

    def scan(self, text: str) -> dict[str, int]:
        """Só o diagnóstico: quantos e de quais tipos (sem devolver valor)."""

        return scan_pii(text, allowed=tuple(self.config.pii_allow or ()))

    # ---- lacuna 5b: multimodal ---------------------------------------
    def remember_media(
        self,
        source: bytes | str | Path,
        *,
        caption: str = "",
        filename: str = "",
        namespace: str | None = None,
        kind: MemoryKind | str = MemoryKind.KNOWLEDGE,
        tags: list[str] | None = None,
        actor: str = "cli",
        task_id: str | None = None,
        agent_id: str | None = None,
        environment: str | None = None,
    ) -> MemoryRecord:
        """Guarda a mídia e memoriza o **lado textual** dela.

        O binário vai para `artifacts/media/`; o que fica buscável é a legenda
        (ou transcrição) declarada por quem escreveu. Sem legenda, o registro
        existe e é recuperável por filtro, mas diz `[sem legenda]` — o Runtime
        não finge ter entendido a imagem.
        """

        payload: bytes | str
        if isinstance(source, Path):
            payload = source.read_bytes()
            filename = filename or source.name
        else:
            payload = source

        asset = self.media.store(
            payload,
            filename=filename,
            caption=caption,
            namespace=namespace or self.default_namespace,
            actor=actor,
        )
        text = asset.caption or f"[{asset.modality} {asset.filename} sem legenda]"
        record = self.write(
            text,
            kind=kind,
            namespace=namespace,
            tags=[*(tags or []), asset.modality],
            source="media",
            task_id=task_id,
            agent_id=agent_id,
            environment=environment,
        )
        record.modality = asset.modality
        record.asset = asset
        record.metadata["mídia"] = asset.summary()
        self.repository.save(record)
        if self.audit:
            self.audit.record(
                EventType.MEMORY_MEDIA_WRITTEN,
                actor=actor,
                environment=environment or "development",
                payload={
                    "memory_id": record.id,
                    "mídia": asset.id,
                    "modalidade": asset.modality,
                    "tipo": asset.mime,
                    "bytes": asset.size,
                    "impressão": asset.fingerprint[:16],
                    "com_legenda": bool(asset.caption),
                },
            )
        return record

    def media_status(self) -> dict[str, Any]:
        state = self.media.status()
        state["registros"] = len([item for item in self.repository.all_records() if item.asset])
        return state

    # knowledge / episodic / semantic shortcuts
    def remember_knowledge(self, content: str, namespace: str = "default", **kwargs) -> MemoryRecord:
        return self.write(content, kind=MemoryKind.KNOWLEDGE, namespace=namespace, **kwargs)

    def remember_episode(self, content: str, namespace: str = "default", **kwargs) -> MemoryRecord:
        return self.write(content, kind=MemoryKind.EPISODIC, namespace=namespace, **kwargs)

    def remember_semantic(self, content: str, namespace: str = "default", **kwargs) -> MemoryRecord:
        """Conhecimento destilado (síntese), não o episódio bruto."""

        return self.write(content, kind=MemoryKind.SEMANTIC, namespace=namespace, **kwargs)

    # ---- read --------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        namespaces: list[str] | None = None,
        kinds: list[str] | None = None,
        limit: int = 5,
        mode: str | None = None,
        include_archived: bool = False,
        reinforce_hits: bool = True,
        explain: bool = False,
    ) -> list[MemoryRecord]:
        """Busca híbrida (padrão), léxica ou semântica."""

        strategy = mode or self.config.retrieval
        multiplier = max(1, self.config.candidate_multiplier)
        pool = max(limit * multiplier, limit)

        lexical: list[tuple[MemoryRecord, float | None]] = []
        if strategy in ("hybrid", "fts"):
            lexical = [
                (record, record.score)
                for record in self.repository.search(
                    query,
                    namespaces=namespaces,
                    kinds=kinds,
                    limit=pool,
                    include_archived=include_archived,
                )
            ]

        semantic: list[tuple[MemoryRecord, float]] = []
        if strategy in ("hybrid", "semantic") and query.strip():
            semantic = self._semantic(query, namespaces, kinds, pool, include_archived)

        if strategy == "fts":
            results = [record for record, _score in lexical][:limit]
        elif strategy == "semantic":
            results = [record for record, _score in semantic][:limit]
        else:
            fused = fuse(
                lexical,
                semantic,
                lexical_weight=self.config.fts_weight,
                semantic_weight=self.config.semantic_weight,
            )
            results = []
            for candidate in fused[:limit]:
                record = candidate.record
                record.score = round(candidate.fused, 6)
                if explain:
                    record.metadata = {**record.metadata, "retrieval": candidate.as_trace()}
                results.append(record)

        if reinforce_hits and results:
            self._reinforce(results)
        return results

    def recall(
        self,
        query: str,
        *,
        namespaces: list[str] | None = None,
        limit: int = 5,
        agent_id: str | None = None,
        task_id: str | None = None,
        environment: str | None = None,
        mode: str | None = None,
    ) -> tuple[list[MemoryRecord], str]:
        """Recall memory and return (records, rendered context for the prompt)."""

        records = self.search(query, namespaces=namespaces, limit=limit, mode=mode) if (
            self.config.auto_recall
        ) else []
        if self.audit and records:
            self.audit.record(
                EventType.MEMORY_RECALLED,
                actor=agent_id or "runtime",
                task_id=task_id,
                agent_id=agent_id,
                environment=environment or "development",
                payload={
                    "query": query,
                    "mode": mode or self.config.retrieval,
                    "hits": [record.id for record in records],
                },
            )
        return records, self.render(records)

    def render(self, records: list[MemoryRecord]) -> str:
        if not records:
            return "(no relevant memory)"
        budget = max(200, self.config.max_context_chars)
        lines: list[str] = []
        used = 0
        for record in records:
            salience_value = salience(record, half_life_days=self.config.half_life_days)
            body = (
                describe(record.asset)
                if record.asset is not None
                else record.summary or record.content[:200]
            )
            line = f"- [{record.kind}/{record.namespace} · {classify(salience_value)}] {body}"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)

    def list(self, namespace: str | None = None, limit: int = 20, include_archived: bool = False):
        return self.repository.list(namespace=namespace, limit=limit, include_archived=include_archived)

    def get(self, record_id: str) -> MemoryRecord | None:
        return self.repository.get(record_id)

    # ---- ciclo de vida -----------------------------------------------
    def forget(self, record_id: str, *, actor: str = "cli") -> bool:
        """Esquece de verdade (registro + FTS + vetor) e deixa rastro."""

        record = self.repository.get(record_id)
        removed = self.repository.delete(record_id)
        if removed and self.audit:
            self.audit.record(
                EventType.SYSTEM_EVENT,
                actor=actor,
                payload={
                    "action": "memory_forgotten",
                    "memory_id": record_id,
                    "namespace": record.namespace if record else None,
                },
            )
        return removed

    def archive(self, record_id: str, *, actor: str = "cli") -> MemoryRecord | None:
        record = self.repository.get(record_id)
        if record is None:
            return None
        record.archived = True
        self.repository.save(record)
        if self.audit:
            self.audit.record(
                EventType.SYSTEM_EVENT,
                actor=actor,
                payload={"action": "memory_archived", "memory_id": record_id},
            )
        return record

    def consolidate(self, *, apply: bool = False, prune: bool = False, actor: str = "cli") -> dict[str, Any]:
        """Detecta near-duplicatas, arquiva as piores e (opcionalmente) poda.

        Sem `--apply` nada muda: o relatório mostra o que aconteceria. É a
        resposta do Runtime para "o que merece ser lembrado?" — decidida por
        política, nunca por acaso.
        """

        config = self.config
        records = [record for record in self.repository.all_records() if not record.archived]
        # mais novo primeiro: em caso de empate de saliência, a versão recente vence
        records.sort(key=lambda item: item.created_at, reverse=True)
        if len(records) > config.max_consolidate_scan:
            records = records[: config.max_consolidate_scan]

        vectors = self._vectors_for([record.id for record in records])
        scored = {record.id: salience(record, half_life_days=config.half_life_days) for record in records}

        duplicates: list[dict[str, Any]] = []
        survivors: list[MemoryRecord] = []
        for record in records:
            vector = vectors.get(record.id)
            if vector is None:
                survivors.append(record)
                continue

            twin: MemoryRecord | None = None
            similarity = 0.0
            for other in survivors:
                other_vector = vectors.get(other.id)
                if other_vector is None:
                    continue
                candidate = embeddings.cosine(vector, other_vector)
                if candidate >= config.duplicate_threshold:
                    twin, similarity = other, candidate
                    break

            if twin is None:
                survivors.append(record)
                continue

            winner, loser = (
                (twin, record) if scored[twin.id] >= scored[record.id] else (record, twin)
            )
            duplicates.append(
                {
                    "winner": winner.id,
                    "loser": loser.id,
                    "cosine": round(similarity, 4),
                    "winner_salience": scored[winner.id],
                    "loser_salience": scored[loser.id],
                }
            )
            if apply:
                loser.archived = True
                loser.duplicate_of = winner.id
                self.repository.save(loser)
                if loser is twin:  # o sobrevivente anterior perdeu: troca na lista
                    survivors.remove(twin)
                    survivors.append(record)

        pruned = 0
        if prune:
            archived = [record for record in self.repository.all_records() if record.archived]
            for record in archived:
                if record.age_days >= config.retention_days:
                    pruned += 1
                    if apply:
                        self.repository.delete(record.id)

        report = {
            "scanned": len(records),
            "duplicates": len(duplicates),
            "prunable": pruned,
            "archived": len(duplicates) if apply else 0,
            "pruned": pruned if apply else 0,
            "applied": apply,
            "pairs": duplicates[:20],
        }
        if self.audit:
            self.audit.record(
                EventType.SYSTEM_EVENT,
                actor=actor,
                payload={"action": "memory_consolidated", **{key: report[key] for key in (
                    "scanned", "duplicates", "prunable", "archived", "pruned", "applied")}},
            )
        return report

    def reindex(self, *, actor: str = "cli") -> dict[str, Any]:
        """Reconstrói todos os vetores (troca de modelo/dimensão ou acervo antigo)."""

        records = self.repository.all_records()
        rebuilt = 0
        for record in records:
            expected = record.embedding_model
            if expected == embeddings.MODEL_ID and self.repository.get_vector(record.id) is not None:
                continue
            self._embed(record)
            rebuilt += 1
        report = {
            "total": len(records),
            "rebuilt": rebuilt,
            "model": embeddings.MODEL_ID,
            "dimension": embeddings.DIMENSION,
        }
        if self.audit:
            self.audit.record(
                EventType.SYSTEM_EVENT,
                actor=actor,
                payload={"action": "memory_reindexed", **report},
            )
        return report

    # ---- stats -------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        records = self.repository.all_records()
        saliences = [salience(record, half_life_days=self.config.half_life_days) for record in records]
        return {
            "total": len(records),
            "active": len([record for record in records if not record.archived]),
            "archived": len([record for record in records if record.archived]),
            "by_kind": self.repository.stats(),
            "by_namespace": self.repository.namespace_stats(),
            "vectors": self.repository.count_vectors(model=embeddings.MODEL_ID),
            "without_vector": len(records) - self.repository.count_vectors(model=embeddings.MODEL_ID),
            "model": embeddings.MODEL_ID,
            "dimension": embeddings.DIMENSION,
            "retrieval": self.config.retrieval,
            # lacuna 5b: multimodal e limpeza de PII
            "por_modalidade": self._modality_stats(records),
            "mídia": self.media_status(),
            "limpeza_pii": {
                "ativa": self.config.scrub_pii,
                "tipos_liberados": list(self.config.pii_allow or []),
                "registros_com_pii_removido": len(
                    [item for item in records if (item.metadata or {}).get("pii_removido")]
                ),
            },
            "avg_salience": round(sum(saliences) / len(saliences), 4) if saliences else 0.0,
            "distribution": {
                state: len([value for value in saliences if classify(value) == state])
                for state in ("viva", "estável", "esfriando", "arquivável")
            },
        }

    @staticmethod
    def _modality_stats(records: list[MemoryRecord]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in records:
            counts[record.modality] = counts.get(record.modality, 0) + 1
        return dict(sorted(counts.items()))

    # ---- internals ---------------------------------------------------
    def _embed(self, record: MemoryRecord) -> None:
        """Projeta e persiste o vetor. Barato o suficiente para ser online."""

        vector = embeddings.embed(f"{record.summary}\n{record.content}")
        self.repository.save_vector(record.id, embeddings.MODEL_ID, embeddings.DIMENSION, vector)
        record.embedding_model = embeddings.MODEL_ID

    def _vectors_for(self, record_ids: list[str]) -> dict[str, list[float]]:
        """Vetores do modelo atual, gerando na hora o que estiver faltando."""

        wanted = set(record_ids)
        vectors = {
            record_id: vector
            for record_id, vector in self.repository.vectors(model=embeddings.MODEL_ID).items()
            if record_id in wanted
        }
        missing = wanted - set(vectors)
        if missing and len(missing) <= 200:  # auto-cura limitada para não travar a busca
            for record_id in missing:
                record = self.repository.get(record_id)
                if record is None:
                    continue
                vector = embeddings.embed(f"{record.summary}\n{record.content}")
                self.repository.save_vector(record_id, embeddings.MODEL_ID, embeddings.DIMENSION, vector)
                vectors[record_id] = vector
        return vectors

    def _semantic(
        self,
        query: str,
        namespaces: list[str] | None,
        kinds: list[str] | None,
        pool: int,
        include_archived: bool,
    ) -> list[tuple[MemoryRecord, float]]:
        total = self.repository.count()
        query_vec = embeddings.embed(query)

        if total <= self.config.max_semantic_scan:
            records = {
                record.id: record
                for record in self.repository.all_records(include_archived=include_archived)
            }
            vectors = self._vectors_for(list(records))
        else:
            # acervo grande: o lado semântico avalia os candidatos do FTS + recentes
            candidates = self.repository.search(
                query, namespaces=namespaces, kinds=kinds, limit=pool * 2, include_archived=include_archived
            )
            recent = self.repository.list(limit=pool * 2, include_archived=include_archived)
            merged = {record.id: record for record in [*candidates, *recent]}
            records = merged
            vectors = self._vectors_for(list(merged))

        filtered = {
            record_id: vector
            for record_id, vector in vectors.items()
            if (namespaces is None or records[record_id].namespace in namespaces)
            and (kinds is None or str(records[record_id].kind) in kinds)
        }
        return rank_by_similarity(query_vec, filtered, records, limit=pool, min_cosine=self.config.min_cosine)

    def _reinforce(self, records: list[MemoryRecord]) -> None:
        """Memória usada fica mais forte — e isso é persistido, não só em RAM."""

        for record in records:
            reinforce(record)
            self.repository.save(record)


__all__ = ["MemoryService"]
