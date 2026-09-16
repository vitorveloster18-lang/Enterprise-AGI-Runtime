from egr.domain.enums import EventType


def test_ledger_is_hash_chained(runtime):
    for index in range(3):
        runtime.audit.record(EventType.SYSTEM_EVENT, actor="test", payload={"index": index})

    events = runtime.audit.list(limit=3)
    assert len(events) == 3
    result = runtime.audit.verify()
    assert result["valid"]
    assert result["events"] == 3


def test_tampering_is_detected(runtime):
    runtime.audit.record(EventType.SYSTEM_EVENT, actor="test", payload={"sigiloso": True})
    assert runtime.audit.verify()["valid"]

    runtime.db.execute("UPDATE events SET payload = '{\"sigiloso\": false}' WHERE actor = 'test'")
    runtime.db.commit()

    result = runtime.audit.verify()
    assert not result["valid"]
    assert result["broken"]


def test_events_carry_governance_context(runtime):
    runtime.submit("Auditar fluxo", agent_id="runtime-agent")
    events = runtime.audit.list(type="tool.executed", limit=10)
    assert events
    for event in events:
        assert event.actor
        assert event.hash and event.prev_hash
