"""Task Engine: create, run, pause-on-approval, resume, cancel."""

from __future__ import annotations

from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.agent import AgentSpec
from ..domain.enums import Environment, EventType, TaskStatus
from ..domain.task import Task, TaskResult


class TaskEngine:
    def __init__(self, runtime):
        self.runtime = runtime

    # ---- lifecycle ---------------------------------------------------
    def create(
        self,
        objective: str,
        agent_id: str | None = None,
        environment: Environment | str | None = None,
        created_by: str = "cli",
        parent_id: str | None = None,
        workflow_id: str | None = None,
    ) -> Task:
        agent = self.runtime.resolve_agent(agent_id)
        task = Task(
            id=new_id("task"),
            objective=objective,
            agent_id=agent.id,
            environment=Environment(environment or self.runtime.settings.environment),
            created_by=created_by,
            parent_id=parent_id,
            workflow_id=workflow_id,
        )
        self.runtime.tasks.save(task)
        self.runtime.audit.record(
            EventType.TASK_CREATED,
            actor=created_by,
            task_id=task.id,
            agent_id=agent.id,
            environment=str(task.environment),
            payload={"objective": objective, "parent": parent_id, "workflow": workflow_id},
        )
        return task

    def run(self, task: Task) -> Task:
        return self.runtime.agent_engine.run(task)

    def submit(
        self,
        objective: str,
        agent_id: str | None = None,
        environment: Environment | str | None = None,
        created_by: str = "cli",
    ) -> Task:
        task = self.create(objective, agent_id=agent_id, environment=environment, created_by=created_by)
        return self.run(task)

    def resume(self, task_id: str, *, approve_pending: bool = True) -> Task:
        task = self.runtime.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id} not found")
        pending = self.runtime.approvals.pending_for_task(task_id)
        if not pending:
            return self.run(task)
        approval = pending[0]
        if not approve_pending:
            return task
        return self.runtime.approve(approval.id, decided_by="cli", note="approved via task resume")

    def cancel(self, task_id: str, reason: str = "cancelled by operator") -> Task:
        task = self.runtime.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id} not found")
        task.status = TaskStatus.CANCELLED
        task.error = reason
        task.finished_at = utcnow()
        self.runtime.tasks.save(task)
        self.runtime.audit.record(
            EventType.TASK_CANCELLED,
            actor="operator",
            task_id=task.id,
            agent_id=task.agent_id,
            environment=str(task.environment),
            payload={"reason": reason},
        )
        return task

    def list(self, status: str | None = None, environment: str | None = None, limit: int = 50):
        return self.runtime.tasks.list(status=status, environment=environment, limit=limit)

    @staticmethod
    def new_result() -> TaskResult:
        return TaskResult()


def agent_for(runtime, agent_id: str | None) -> AgentSpec:
    return runtime.resolve_agent(agent_id)
