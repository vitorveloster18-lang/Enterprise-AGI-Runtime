from egr.domain.enums import TaskStatus


def test_task_runs_locally_end_to_end(runtime):
    task = runtime.submit(
        "Analise os documentos desta pasta e produza um relatório.",
        agent_id="document-agent",
    )

    assert task.status == TaskStatus.COMPLETED, task.error
    assert [step.tool for step in task.result.steps] == ["filesystem.list", "python.execute"]
    assert all(step.ok for step in task.result.steps)
    assert task.result.artifacts

    events = runtime.audit.list(task_id=task.id, limit=100)
    types = {event.type for event in events}
    assert {"task.created", "plan.created", "policy.allowed", "tool.executed", "task.completed"} <= types

    artifacts = runtime.artifacts.list(task_id=task.id)
    assert artifacts and artifacts[0].name == "relatorio.md"


def test_production_pauses_for_human_approval(runtime):
    task = runtime.submit("Gerar relatório consolidado", environment="production")
    assert task.status == TaskStatus.REQUIRES_APPROVAL

    pending = runtime.approvals.pending_for_task(task.id)
    assert len(pending) == 1
    assert pending[0].tool == "python.execute"
    assert pending[0].required_role == "operator"

    resumed = runtime.approve(pending[0].id, decided_by="vitor", note="revisado")
    assert resumed.status == TaskStatus.COMPLETED
    assert any(step.decision == "approved" for step in resumed.result.steps)
    assert runtime.approvals.get(pending[0].id).status == "approved"


def test_denied_approval_keeps_the_task_going(runtime):
    task = runtime.submit("Gerar relatório consolidado", environment="production")
    pending = runtime.approvals.pending_for_task(task.id)[0]
    task = runtime.deny(pending.id, decided_by="vitor", note="não agora")
    assert task.status == TaskStatus.COMPLETED
    assert any(step.decision == "denied_by_human" for step in task.result.steps)


def test_task_writes_operational_and_episodic_memory(runtime):
    runtime.submit("Analisar documentos", agent_id="document-agent")
    operational = runtime.memory.search("filesystem.list", namespaces=["documents"], limit=5)
    episodic = runtime.memory.search("Analisar documentos", namespaces=["documents"], limit=5)
    assert operational
    assert any(record.kind == "episodic" for record in episodic)


def test_subtask_relationship_is_recorded(runtime):
    parent = runtime.task_engine.create("pai", agent_id="runtime-agent")
    child = runtime.task_engine.create("filho", agent_id="runtime-agent", parent_id=parent.id)
    assert child.parent_id == parent.id
