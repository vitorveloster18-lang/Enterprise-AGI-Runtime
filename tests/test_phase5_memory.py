"""Fase 5 — Memory System: semântica local, recuperação híbrida e ciclo de vida."""

from __future__ import annotations

import pytest

from egr.core.config import EGRConfig, Settings
from egr.domain.enums import MemoryKind
from egr.memory import embeddings
from egr.memory.salience import classify, infer_importance, reinforce, salience
from egr.runtime.runtime import Runtime


@pytest.fixture()
def runtime(workspace):
    instance = Runtime(Settings(workspace=workspace, config=EGRConfig()), enable_logging=False)
    yield instance
    instance.close()


def _seed(runtime) -> None:
    runtime.memory.remember_knowledge(
        "O limite de aprovação automática de pagamentos é de R$ 5.000,00",
        namespace="finance",
        tags=["politica", "financeiro"],
    )
    runtime.memory.remember_knowledge(
        "Toda despesa precisa de nota fiscal vinculada ao pedido de compra",
        namespace="finance",
    )
    runtime.memory.remember_episode("Relatório de agosto gerado com 3 documentos", namespace="finance")


# ----------------------------------------------------------------------
# embedding determinístico
# ----------------------------------------------------------------------
def test_embedding_is_deterministic_and_normalized():
    first = embeddings.embed("limite de aprovação de pagamentos")
    second = embeddings.embed("limite de aprovação de pagamentos")

    assert first == second
    assert len(first) == embeddings.DIMENSION
    assert abs(sum(component * component for component in first) - 1.0) < 1e-6


def test_embedding_separates_related_from_unrelated_text():
    related = embeddings.cosine(
        embeddings.embed("limite de aprovação automática de pagamentos"),
        embeddings.embed("aprovação automática de pagamentos até 5000"),
    )
    unrelated = embeddings.cosine(
        embeddings.embed("limite de aprovação automática de pagamentos"),
        embeddings.embed("receita de bolo de cenoura com cobertura"),
    )

    assert related > 0.3
    assert unrelated < 0.1
    assert related > unrelated


def test_stopwords_do_not_dominate_the_vector_space():
    """Sem stopwords, uma palavra banal domina o cosseno e a memória degrada."""

    left = embeddings.embed("relatório de receita de agosto")
    other = embeddings.embed("relatório de receita de agosto")
    noise = embeddings.embed("política")

    assert embeddings.cosine(left, other) == pytest.approx(1.0, abs=1e-6)
    assert "de" not in embeddings.tokenize("relatório de receita de agosto")
    # stopword removida: o que sobra discrimina, em vez de inflar o cosseno
    assert embeddings.cosine(left, noise) < 0.05


def test_empty_text_produces_a_zero_vector():
    assert embeddings.embed("") == [0.0] * embeddings.DIMENSION
    assert embeddings.cosine(embeddings.embed(""), embeddings.embed("algo")) == 0.0


# ----------------------------------------------------------------------
# recuperação híbrida
# ----------------------------------------------------------------------
def test_hybrid_search_finds_by_meaning_not_only_by_keyword(runtime):
    _seed(runtime)

    hits = runtime.memory.search("liberar pagamento sem aprovação", namespaces=["finance"], limit=3)

    assert hits
    assert "limite" in hits[0].summary.lower()


def test_search_modes_change_the_ranking_source(runtime):
    _seed(runtime)

    lexical = runtime.memory.search("nota fiscal", namespaces=["finance"], limit=3, mode="fts")
    semantic = runtime.memory.search("nota fiscal", namespaces=["finance"], limit=3, mode="semantic")

    assert lexical and semantic
    assert all(record.score is not None for record in semantic)


def test_hybrid_search_explains_the_fusion(runtime):
    _seed(runtime)

    hits = runtime.memory.search("aprovação de pagamentos", namespaces=["finance"], limit=2, explain=True)

    trace = hits[0].metadata["retrieval"]
    assert "fts_rank" in trace and "semantic_rank" in trace and "cosine" in trace


def test_semantic_weight_zero_disables_the_semantic_side(runtime):
    runtime.settings.config.memory.semantic_weight = 0.0
    _seed(runtime)

    hits = runtime.memory.search("liberar pagamento sem aprovação", namespaces=["finance"], limit=3)

    assert all(record.metadata.get("retrieval", {}).get("cosine") is None for record in hits)


def test_recall_respects_namespace_isolation(runtime):
    runtime.memory.remember_knowledge("Política de frete", namespace="logistica")

    assert runtime.memory.search("frete", namespaces=["logistica"], limit=3)
    assert not runtime.memory.search("frete", namespaces=["finance"], limit=3)


def test_reinforcement_makes_used_memory_stronger(runtime):
    _seed(runtime)
    before = runtime.memory.search("aprovação de pagamentos", namespaces=["finance"], limit=1)[0]

    assert before.access_count == 1
    runtime.memory.search("aprovação de pagamentos", namespaces=["finance"], limit=1)
    after = runtime.memory.get(before.id)

    assert after.access_count == 2
    assert after.last_accessed_at is not None


def test_saving_twice_does_not_duplicate_the_fts_index(runtime):
    """Reforço reescreve o registro; sem limpar o FTS a memória se duplica."""

    record = runtime.memory.remember_knowledge("Regra única de conciliação", namespace="finance")

    reinforce(record)
    runtime.memory.repository.save(record)
    reinforce(record)
    runtime.memory.repository.save(record)

    hits = runtime.memory.search("conciliação", namespaces=["finance"], limit=10)
    assert len([item for item in hits if item.id == record.id]) == 1


# ----------------------------------------------------------------------
# saliência e ciclo de vida
# ----------------------------------------------------------------------
def test_importance_is_inferred_from_kind_and_content():
    policy = infer_importance("Política de gastos: limite de R$ 5.000,00", kind=MemoryKind.KNOWLEDGE)
    episode = infer_importance("task executada", kind=MemoryKind.EPISODIC)

    assert policy > episode
    assert 0.0 < episode <= 1.0


def test_salience_decays_with_time_and_grows_with_use():
    from datetime import timedelta

    from egr.core.timeutil import utcnow

    record = runtime = None  # placeholder to keep linters honest
    del record, runtime

    from egr.domain.memory import MemoryRecord

    fresh = MemoryRecord(id="mem_1", content="x", importance=0.8, created_at=utcnow())
    old = MemoryRecord(id="mem_2", content="x", importance=0.8, created_at=utcnow() - timedelta(days=30))

    assert salience(old, half_life_days=30) < salience(fresh, half_life_days=30)

    reinforced = reinforce(fresh)
    assert reinforced.access_count == 1
    assert salience(reinforced, half_life_days=30) > salience(fresh, half_life_days=30) * 0.99


def test_classify_bands():
    assert classify(0.9) == "viva"
    assert classify(0.4) == "estável"
    assert classify(0.2) == "esfriando"
    assert classify(0.01) == "arquivável"


# ----------------------------------------------------------------------
# consolidação
# ----------------------------------------------------------------------
def test_consolidate_detects_near_duplicates_and_keeps_the_best(runtime):
    original = runtime.memory.remember_knowledge(
        "O limite de aprovação automática de pagamentos é de R$ 5.000,00",
        namespace="finance",
    )
    copy = runtime.memory.remember_knowledge(
        "O limite de aprovacao automatica de pagamentos e de R$ 5.000,00",
        namespace="finance",
    )

    report = runtime.memory.consolidate()

    assert report["duplicates"] == 1
    assert report["archived"] == 0  # sem --apply nada muda
    assert runtime.memory.get(copy.id).archived is False

    applied = runtime.memory.consolidate(apply=True)
    assert applied["archived"] == 1

    survivor_id = applied["pairs"][0]["winner"]
    loser = runtime.memory.get(applied["pairs"][0]["loser"])
    assert loser.archived is True
    assert loser.duplicate_of == survivor_id
    assert survivor_id in {original.id, copy.id}


def test_archived_memory_leaves_the_default_search(runtime):
    record = runtime.memory.remember_knowledge("Regra obsoleta de frete", namespace="logistica")

    runtime.memory.archive(record.id)

    assert runtime.memory.search("frete", namespaces=["logistica"], limit=5) == []
    assert runtime.memory.search("frete", namespaces=["logistica"], limit=5, include_archived=True)


def test_prune_only_removes_when_applied(runtime):
    record = runtime.memory.remember_knowledge("Memória antiga", namespace="default")
    runtime.memory.archive(record.id)
    runtime.settings.config.memory.retention_days = 0

    report = runtime.memory.consolidate(prune=True)
    assert report["prunable"] == 1
    assert report["pruned"] == 0
    assert runtime.memory.get(record.id) is not None

    applied = runtime.memory.consolidate(prune=True, apply=True)
    assert applied["pruned"] == 1
    assert runtime.memory.get(record.id) is None


def test_forget_removes_record_index_and_vector(runtime):
    record = runtime.memory.remember_knowledge("Segredo temporário", namespace="default")

    assert runtime.memory.forget(record.id) is True

    assert runtime.memory.get(record.id) is None
    assert runtime.memory.repository.get_vector(record.id) is None
    assert runtime.memory.search("Segredo temporário", limit=5) == []


# ----------------------------------------------------------------------
# reindexação e vetores
# ----------------------------------------------------------------------
def test_write_stores_a_vector_of_the_current_model(runtime):
    record = runtime.memory.remember_knowledge("Regra de conciliação bancária", namespace="finance")

    model, vector = runtime.memory.repository.get_vector(record.id)

    assert model == embeddings.MODEL_ID
    assert len(vector) == embeddings.DIMENSION


def test_reindex_rebuilds_missing_and_stale_vectors(runtime):
    record = runtime.memory.remember_knowledge("Regra X", namespace="default")
    runtime.memory.repository.delete_vector(record.id)

    report = runtime.memory.reindex()

    assert report["rebuilt"] == 1
    assert runtime.memory.repository.get_vector(record.id) is not None


def test_search_self_heals_records_without_vectors(runtime):
    record = runtime.memory.remember_knowledge("Política de reembolso de despesas", namespace="finance")
    runtime.memory.repository.delete_vector(record.id)

    hits = runtime.memory.search("reembolso de despesas", namespaces=["finance"], limit=3, mode="semantic")

    assert hits
    assert runtime.memory.repository.get_vector(record.id) is not None


def test_stats_expose_vectors_and_lifecycle(runtime):
    _seed(runtime)

    data = runtime.memory.stats()

    assert data["model"] == embeddings.MODEL_ID
    assert data["vectors"] == data["total"]
    assert data["without_vector"] == 0
    assert data["by_namespace"]["finance"] == 3
    assert sum(data["distribution"].values()) == data["total"]
    assert data["retrieval"] == "hybrid"


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def test_api_memory_endpoints(runtime):
    from fastapi.testclient import TestClient

    from egr.api.server import create_app

    client = TestClient(create_app(runtime))
    created = client.post(
        "/v1/memory",
        json={"content": "Limite de aprovação automática: R$ 5.000,00", "namespace": "finance"},
    )

    assert created.status_code == 200
    assert created.json()["embedding_model"] == embeddings.MODEL_ID

    hits = client.get("/v1/memory", params={"query": "aprovar pagamento", "limit": 3, "explain": True})
    assert hits.status_code == 200
    assert hits.json()

    stats = client.get("/v1/memory/stats").json()
    assert stats["total"] == 1
    assert stats["vectors"] == 1

    bad = client.post("/v1/memory", json={"content": "x", "kind": "inventado"})
    assert bad.status_code == 422
