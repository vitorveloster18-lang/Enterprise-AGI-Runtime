"""Agent Engine: the governed execution loop.

    TASK -> AGENT -> (MEMORY + MODEL) -> PLAN -> ACTION PROPOSAL -> POLICY
          -> ALLOW | DENY | APPROVAL -> TOOL -> RESULT -> MEMORY -> AUDIT
"""

from __future__ import annotations

import time

from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.agent import AgentSpec
from ..domain.approval import Approval
from ..domain.artifact import Artifact
from ..domain.enums import (
    ArtifactKind,
    Environment,
    EventType,
    MemoryKind,
    TaskStatus,
)
from ..domain.task import StepRecord, Task
from ..domain.tool import ToolRequest
from ..security.redaction import redact_mapping
from .planner import Plan, PlanParseError, build_plan_messages, fallback_plan, parse_plan


class AgentEngine:
    def __init__(self, runtime):
        self.runtime = runtime

    # ---- planning ----------------------------------------------------
    def plan(self, task: Task, agent: AgentSpec) -> Plan:
        records, memory_context = self.runtime.memory.recall(
            task.objective,
            namespaces=agent.memory or ["default"],
            limit=5,
            agent_id=agent.id,
            task_id=task.id,
            environment=str(task.environment),
        )
        catalog = self.runtime.tools.catalog(allowed=agent.permissions.tools or None)
        messages = build_plan_messages(
            agent=agent,
            objective=task.objective,
            tool_catalog=catalog,
            memory_context=memory_context,
            environment=task.environment,
            max_steps=self.runtime.settings.config.runtime.max_steps,
        )
        request_kwargs = dict(
            task_id=task.id,
            agent_id=agent.id,
            environment=str(task.environment),
        )
        try:
            response = self.runtime.gateway.complete(
                self.runtime.completion_request(messages, agent),
                allow_external=agent.model.allow_external,
                **request_kwargs,
            )
        except Exception as exc:
            self.runtime.audit.record(
                EventType.PLAN_FAILED,
                actor=agent.id,
                task_id=task.id,
                agent_id=agent.id,
                environment=str(task.environment),
                payload={"error": f"{type(exc).__name__}: {exc}"},
            )
            return fallback_plan(task.objective)

        try:
            plan = parse_plan(response.text, task.objective)
        except PlanParseError as exc:
            self.runtime.audit.record(
                EventType.PLAN_FAILED,
                actor=agent.id,
                task_id=task.id,
                agent_id=agent.id,
                environment=str(task.environment),
                payload={"error": str(exc), "raw": response.text[:1000]},
            )
            return fallback_plan(task.objective)

        plan.provider = response.provider
        plan.model = response.model
        # enforce the runtime budget, not the model's optimism
        max_steps = self.runtime.settings.config.runtime.max_steps
        if len(plan.steps) > max_steps:
            plan.steps = plan.steps[:max_steps]

        self.runtime.audit.record(
            EventType.PLAN_CREATED,
            actor=agent.id,
            task_id=task.id,
            agent_id=agent.id,
            environment=str(task.environment),
            payload={
                "steps": [
                    {"id": step.id, "tool": step.tool, "rationale": step.rationale}
                    for step in plan.steps
                ],
                "provider": plan.provider,
                "model": plan.model,
                "memory_hits": [record.id for record in records],
            },
        )
        return plan

    # ---- execution ---------------------------------------------------
    def run(self, task: Task) -> Task:
        runtime = self.runtime
        agent = runtime.agents.get(task.agent_id) or runtime.default_agent()
        started = time.perf_counter()

        runtime.audit.record(
            EventType.TASK_STARTED,
            actor=task.created_by,
            task_id=task.id,
            agent_id=agent.id,
            environment=str(task.environment),
            payload={"objective": task.objective, "resumed": bool(task.context.get("cursor"))},
        )
        runtime.audit.record(
            EventType.AGENT_LOADED,
            actor=agent.id,
            task_id=task.id,
            agent_id=agent.id,
            environment=str(task.environment),
            payload={"agent": agent.id, "version": agent.version, "capability": agent.model.capability},
        )

        task.status = TaskStatus.RUNNING
        task.started_at = task.started_at or utcnow()
        if task.result is None:
            task.result = self.runtime.new_result()
        runtime.tasks.save(task)

        # plan (reused on resume)
        plan_data = task.context.get("plan")
        if plan_data:
            plan = Plan.model_validate(plan_data)
        else:
            plan = self.plan(task, agent)
            task.context["plan"] = plan.model_dump(mode="json")
            task.result.plan = plan.model_dump(mode="json")
            task.result.model = f"{plan.provider}:{plan.model}" if plan.model else (plan.provider or "-")
            runtime.tasks.save(task)

        cursor = int(task.context.get("cursor", 0))
        steps = plan.steps
        tool_ctx = self.runtime.tool_context(task, agent)

        while cursor < len(steps):
            step = steps[cursor]
            request = ToolRequest(
                tool=step.tool,
                args=dict(step.args or {}),
                action=step.action,
                task_id=task.id,
                agent_id=agent.id,
                step_id=step.id,
                environment=task.environment,
                rationale=step.rationale,
            )
            runtime.audit.record(
                EventType.ACTION_PROPOSED,
                actor=agent.id,
                task_id=task.id,
                agent_id=agent.id,
                environment=str(task.environment),
                payload={
                    "step": step.id,
                    "tool": step.tool,
                    "args": redact_mapping(step.args or {}),
                    "rationale": step.rationale,
                },
            )

            decision = self.runtime.authorize(request, agent)
            if decision.needs_approval and self._auto_approve(task):
                decision = decision.model_copy(
                    update={"decision": "allow", "reason": "auto-approved in development"}
                )

            if decision.needs_approval:
                approval = self._request_approval(task, agent, request, decision, step.id)
                task.context["cursor"] = cursor
                task.context["pending_approval"] = approval.id
                task.context["pending_step"] = step.id
                task.status = TaskStatus.REQUIRES_APPROVAL
                runtime.tasks.save(task)
                runtime.audit.record(
                    EventType.TASK_WAITING,
                    actor=agent.id,
                    task_id=task.id,
                    agent_id=agent.id,
                    environment=str(task.environment),
                    payload={"approval": approval.id, "step": step.id, "tool": step.tool},
                )
                return task

            if not decision.allowed:
                runtime.audit.record(
                    EventType.POLICY_DENIED,
                    actor=agent.id,
                    task_id=task.id,
                    agent_id=agent.id,
                    environment=str(task.environment),
                    payload={
                        "step": step.id,
                        "tool": step.tool,
                        "rule": decision.rule_id,
                        "reason": decision.reason,
                    },
                )
                task.result.steps.append(
                    StepRecord(
                        id=step.id,
                        tool=step.tool,
                        args=redact_mapping(step.args or {}),
                        rationale=step.rationale,
                        decision="deny",
                        rule_id=decision.rule_id,
                        ok=False,
                        error=decision.reason,
                    )
                )
                cursor += 1
                task.context["cursor"] = cursor
                runtime.tasks.save(task)
                continue

            runtime.audit.record(
                EventType.POLICY_ALLOWED,
                actor=agent.id,
                task_id=task.id,
                agent_id=agent.id,
                environment=str(task.environment),
                payload={"step": step.id, "tool": step.tool, "rule": decision.rule_id},
            )
            result = self.runtime.tools.execute(step.tool, step.args or {}, tool_ctx)
            runtime.audit.record(
                EventType.TOOL_EXECUTED if result.ok else EventType.TOOL_FAILED,
                actor=agent.id,
                task_id=task.id,
                agent_id=agent.id,
                environment=str(task.environment),
                payload={
                    "step": step.id,
                    "tool": step.tool,
                    "ok": result.ok,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                    "output_preview": self._preview(result.output),
                },
            )

            record = StepRecord(
                id=step.id,
                tool=step.tool,
                args=redact_mapping(step.args or {}),
                rationale=step.rationale,
                decision="allow",
                rule_id=decision.rule_id,
                ok=result.ok,
                output=self._preview(result.output, limit=1500),
                error=result.error,
                duration_ms=result.duration_ms,
            )
            task.result.steps.append(record)

            if result.ok:
                self._capture_artifacts(task, agent, result)
                self._remember_step(task, agent, step, result)

            cursor += 1
            task.context["cursor"] = cursor
            task.context.pop("pending_approval", None)
            task.context.pop("pending_step", None)
            runtime.tasks.save(task)

        return self._finish(task, agent, plan, started)

    # ---- resume ------------------------------------------------------
    def resume_after_approval(self, task: Task, approval: Approval) -> Task:
        """Execute the step that was waiting for a human, then continue the loop."""

        runtime = self.runtime
        agent = runtime.agents.get(task.agent_id) or runtime.default_agent()
        plan = Plan.model_validate(task.context.get("plan") or {})
        step_id = task.context.get("pending_step")
        step = next((item for item in plan.steps if item.id == step_id), None)
        if step is None:
            return self.run(task)

        tool_ctx = self.runtime.tool_context(task, agent)
        request = ToolRequest(
            tool=step.tool,
            args=dict(step.args or {}),
            action=step.action,
            task_id=task.id,
            agent_id=agent.id,
            step_id=step.id,
            environment=task.environment,
            rationale=step.rationale,
        )
        decision = self.runtime.authorize(request, agent)
        if not decision.allowed and not decision.needs_approval:
            task.result.steps.append(
                StepRecord(
                    id=step.id,
                    tool=step.tool,
                    args=redact_mapping(step.args or {}),
                    decision="deny",
                    ok=False,
                    error=decision.reason,
                )
            )
            task.context["cursor"] = int(task.context.get("cursor", 0)) + 1
            runtime.tasks.save(task)
            return self.run(task)

        result = self.runtime.tools.execute(step.tool, step.args or {}, tool_ctx)
        runtime.audit.record(
            EventType.TOOL_EXECUTED if result.ok else EventType.TOOL_FAILED,
            actor=agent.id,
            task_id=task.id,
            agent_id=agent.id,
            environment=str(task.environment),
            payload={
                "step": step.id,
                "tool": step.tool,
                "ok": result.ok,
                "approval": approval.id,
                "error": result.error,
            },
        )
        task.result.steps.append(
            StepRecord(
                id=step.id,
                tool=step.tool,
                args=redact_mapping(step.args or {}),
                rationale=step.rationale,
                decision="approved",
                approval_id=approval.id,
                ok=result.ok,
                output=self._preview(result.output, limit=1500),
                error=result.error,
                duration_ms=result.duration_ms,
            )
        )
        if result.ok:
            self._capture_artifacts(task, agent, result)
        task.context["cursor"] = int(task.context.get("cursor", 0)) + 1
        task.context.pop("pending_approval", None)
        task.context.pop("pending_step", None)
        task.status = TaskStatus.RUNNING
        runtime.tasks.save(task)
        return self.run(task)

    # ---- helpers -----------------------------------------------------
    def _auto_approve(self, task: Task) -> bool:
        return bool(
            self.runtime.settings.config.runtime.auto_approve_in_development
            and task.environment == Environment.DEVELOPMENT
        )

    def _request_approval(self, task, agent, request, decision, step_id) -> Approval:
        for approval in self.runtime.approvals.pending_for_task(task.id):
            if approval.step_id == step_id:
                return approval
        return self.runtime.request_approval(
            request,
            decision,
            agent=agent,
            task_id=task.id,
            step_id=step_id,
            environment=task.environment,
        )

    def _capture_artifacts(self, task: Task, agent: AgentSpec, result) -> None:
        for item in result.artifacts or []:
            kind = ArtifactKind.REPORT if str(item.get("name", "")).endswith((".md", ".txt")) else ArtifactKind.FILE
            artifact = Artifact(
                id=new_id("artifact"),
                kind=kind,
                name=item.get("name", "artifact"),
                path=item.get("path"),
                content_type=item.get("content_type", "text/markdown"),
                summary=f"Produzido por {agent.id} na task {task.id}",
                task_id=task.id,
                agent_id=agent.id,
                environment=task.environment,
                metadata={"bytes": item.get("bytes"), "absolute": item.get("absolute")},
            )
            self.runtime.artifacts.save(artifact)
            task.result.artifacts.append(artifact.id)
            self.runtime.audit.record(
                EventType.ARTIFACT_CREATED,
                actor=agent.id,
                task_id=task.id,
                agent_id=agent.id,
                environment=str(task.environment),
                payload={"artifact": artifact.id, "name": artifact.name, "path": artifact.path},
            )

    def _remember_step(self, task: Task, agent: AgentSpec, step, result) -> None:
        if not result.ok:
            return
        summary = f"{step.tool}: {self._preview(result.output, limit=400)}"
        self.runtime.memory.write(
            content=summary,
            kind=MemoryKind.OPERATIONAL,
            namespace=(agent.memory or ["default"])[0],
            summary=f"[{task.id}] {step.tool}",
            tags=[step.tool, str(task.environment)],
            source="tool",
            task_id=task.id,
            agent_id=agent.id,
            environment=str(task.environment),
        )

    def _finish(self, task: Task, agent: AgentSpec, plan: Plan, started: float) -> Task:
        runtime = self.runtime
        failures = [step for step in task.result.steps if step.ok is False]
        successes = [step for step in task.result.steps if step.ok is True]
        task.result.duration_ms = int((time.perf_counter() - started) * 1000)
        task.result.answer = plan.final_answer or self._synthesize(task, plan)
        task.finished_at = utcnow()

        if successes and failures:
            task.status = TaskStatus.COMPLETED
            task.error = f"{len(failures)} step(s) failed"
        elif failures and not successes and task.result.steps:
            task.status = TaskStatus.FAILED
            task.error = failures[0].error or "all steps failed"
        else:
            task.status = TaskStatus.COMPLETED

        runtime.tasks.save(task)

        runtime.memory.write(
            content=(
                f"Objetivo: {task.objective}\n"
                f"Resultado: {task.result.answer}\n"
                f"Passos: {len(task.result.steps)} "
                f"({len(successes)} ok, {len(failures)} falhas)\n"
                f"Artefatos: {', '.join(task.result.artifacts) or '-'}"
            ),
            kind=MemoryKind.EPISODIC,
            namespace=(agent.memory or ["default"])[0],
            summary=f"[episodio] {task.objective[:120]}",
            tags=["task", str(task.status), str(task.environment)],
            source="runtime",
            task_id=task.id,
            agent_id=agent.id,
            environment=str(task.environment),
        )

        runtime.audit.record(
            EventType.TASK_COMPLETED if task.status == TaskStatus.COMPLETED else EventType.TASK_FAILED,
            actor=agent.id,
            task_id=task.id,
            agent_id=agent.id,
            environment=str(task.environment),
            payload={
                "status": str(task.status),
                "steps": len(task.result.steps),
                "failures": len(failures),
                "artifacts": task.result.artifacts,
                "duration_ms": task.result.duration_ms,
                "error": task.error,
            },
        )
        return task

    @staticmethod
    def _synthesize(task: Task, plan: Plan) -> str:
        lines = [f"Objetivo: {task.objective}", "", "Passos executados:"]
        for step in task.result.steps:
            status = "ok" if step.ok else ("negado" if step.decision == "deny" else "falhou")
            lines.append(f"- {step.id} {step.tool}: {status}")
        if task.result.artifacts:
            lines.append("")
            lines.append(f"Artefatos: {len(task.result.artifacts)}")
        return "\n".join(lines)

    @staticmethod
    def _preview(output, limit: int = 500) -> str:
        if output is None:
            return ""
        if isinstance(output, str):
            text = output
        else:
            try:
                import json as _json

                text = _json.dumps(output, ensure_ascii=False, default=str)
            except Exception:
                text = str(output)
        return text if len(text) <= limit else text[:limit] + "..."
