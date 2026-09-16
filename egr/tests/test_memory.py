def test_memory_write_and_search(runtime):
    runtime.memory.remember_knowledge(
        "O limite de aprovação automática de pagamentos é de R$ 5.000,00",
        namespace="finance",
    )
    hits = runtime.memory.search("limite de aprovação", namespaces=["finance"], limit=3)
    assert hits
    assert hits[0].namespace == "finance"


def test_memory_is_namespace_isolated(runtime):
    runtime.memory.remember_knowledge("Política de frete", namespace="logistica")
    assert runtime.memory.search("frete", namespaces=["logistica"], limit=3)
    assert not runtime.memory.search("frete", namespaces=["finance"], limit=3)


def test_memory_stats_and_episodes(runtime):
    runtime.memory.remember_knowledge("Regra de negócio X", namespace="default")
    runtime.memory.remember_episode("task executada com sucesso", namespace="default")
    stats = runtime.memory.stats()
    assert stats["total"] >= 2
    assert "episodic" in stats["by_kind"]
    assert "knowledge" in stats["by_kind"]
