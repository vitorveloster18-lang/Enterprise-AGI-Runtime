"""Workflow Engine: DAG, retry, condição, compensação e retomada.

Regras que este motor não negocia:

* **cada passo é uma task** — passa por agente, política, ferramenta e auditoria;
  orquestração não é um atalho para pular governança;
* **a execução é um objeto** (`WorkflowRun`) persistido e retomável;
* **aprovação pausa o run** (`waiting`), não falha nem segue em frente;
* **dependência quebrada propaga**: passo a jusante de uma falha tolerada é
  `skipped`, nunca executado como se estivesse tudo bem;
* **retry é limitado** e cada tentativa fica registrada (`attempts`).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..core.errors import ConditionError, ConfigError
from ..core.ids import new_id
from ..core.templating import render
from ..core.timeutil import utcnow
from ..domain.enums import EventType, RunStatus, StepRunStatus, TaskStatus
from ..domain.run import WorkflowRun, WorkflowStepRun
from ..domain.workflow import Workflow, WorkflowStep
from ..policies.conditions import Condition

STEP_TEMPLATE = "Executar {tool} com os argumentos {args}"


class WorkflowEngine:
    def __init__(self, runtime):
        self.runtime = runtime

    # ---- validação ----------------------------------------------------
    def validate(self, workflow: Workflow) -> list[str]:
        """Problemas que impedem (ou comprometem) a execução."""

        problems: list[str] = []
        ids = [step.id for step in workflow.steps]
        duplicates = {step_id for step_id in ids if ids.count(step_id) > 1}
        for step_id in sorted(duplicates):
            problems.append(f"passo duplicado: {step_id}")

        known = set(ids)
        for step in workflow.steps:
            for dependency in step.depends_on:
                if dependency not in known:
                    problems.append(f"passo '{step.id}' depende de '{dependency}', que não existe")
            if not step.objective and not step.tool:
                problems.append(f"passo '{step.id}' sem 'objective' e sem 'tool'")
            if step.on_error and step.on_error not in ("fail", "continue", "compensate"):
                problems.append(f"passo '{step.id}' com on_error inválido: {step.on_error}")
            if step.on_error == "compensate" and not step.compensate_with:
                problems.append(f"passo '{step.id}' pede compensação sem 'compensate_with'")
            if step.compensate_with and step.compensate_with not in known:
                problems.append(f"passo '{step.id}' compensa com '{step.compensate_with}', que não existe")
            if step.condition:
                try:
                    Condition(step.condition)
                except ConditionError as exc:
                    problems.append(f"passo '{step.id}' com condição inválida: {exc}")

        try:
            self.levels(workflow)
        except ConfigError as exc:
            problems.append(str(exc))

        if workflow.trigger.type == "cron":
            from ..orchestration.cron import CronError, CronExpression

            try:
                CronExpression(workflow.trigger.cron or "")
            except CronError as exc:
                problems.append(f"cron inválido: {exc}")
        return problems

    def levels(self, workflow: Workflow) -> list[list[WorkflowStep]]:
        """Ordenação topológica em níveis (Kahn). Ciclo => ConfigError."""

        by_id = {step.id: step for step in workflow.steps}
        remaining = {step.id: set(step.depends_on) for step in workflow.steps}
        resolved: set[str] = set()
        ordered: list[list[WorkflowStep]] = []

        while remaining:
            ready = sorted(
                step_id for step_id, deps in remaining.items() if deps <= resolved
            )
            if not ready:
                cycle = ", ".join(sorted(remaining))
                raise ConfigError(f"ciclo de dependências detectado entre os passos: {cycle}")
            ordered.append([by_id[step_id] for step_id in ready])
            for step_id in ready:
                remaining.pop(step_id)
                resolved.add(step_id)
        return ordered

    # ---- ciclo de vida ------------------------------------------------
    def start(
        self,
        workflow_id: str,
        *,
        inputs: dict | None = None,
        environment: str | None = None,
        created_by: str = "cli",
        trigger: str = "manual",
        trigger_detail: str = "",
    ) -> WorkflowRun:
        workflow = self._require(workflow_id)
        # Workflow inválido não executa: orquestração é declaração, e declaração
        # errada é erro de configuração, não comportamento silencioso em runtime.
        problems = self.validate(workflow)
        if problems:
            raise ConfigError(f"workflow '{workflow_id}' inválido: {'; '.join(problems)}")

        run = WorkflowRun(
            id=new_id("run"),
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            environment=environment or str(workflow.environment),
            trigger=trigger,
            trigger_detail=trigger_detail,
            inputs={**workflow.inputs, **(inputs or {})},
            created_by=created_by,
            steps=[WorkflowStepRun(id=step.id) for step in workflow.steps],
        )
        self.runtime.runs.save(run)
        self.runtime.audit.record(
            EventType.SYSTEM_EVENT,
            actor=created_by,
            environment=str(run.environment),
            payload={
                "action": "workflow_started",
                "run": run.id,
                "workflow": workflow.id,
                "version": workflow.version,
                "trigger": f"{trigger}{f':{trigger_detail}' if trigger_detail else ''}",
                "problems": problems,
            },
        )
        return self.execute(run)

    def execute(self, run: WorkflowRun) -> WorkflowRun:
        """Executa (ou continua) o run até o fim, até pausar ou até falhar."""

        workflow = self._require(run.workflow_id)
        if run.finished:
            return run

        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or utcnow()
        self.runtime.runs.save(run)

        for level in self.levels(workflow):
            self._skip_blocked(workflow, run, level)
            pending = [step for step in level if not run.step(step.id).terminal]
            if not pending:
                continue

            outcomes = self._run_level(workflow, run, pending)

            if any(item == StepRunStatus.WAITING for item in outcomes):
                run.status = RunStatus.WAITING
                self.runtime.runs.save(run)
                return run

            failed = [
                step
                for step, outcome in zip(pending, outcomes, strict=False)
                if outcome == StepRunStatus.FAILED
            ]
            if failed:
                _tolerated, blocking = self._classify_failures(workflow, run, failed)
                if blocking:
                    run.status = RunStatus.FAILED
                    run.error = f"passo(s) falharam sem tolerância: {', '.join(step.id for step in blocking)}"
                    run.finished_at = utcnow()
                    self.runtime.runs.save(run)
                    self._record_finish(run)
                    return run

        self._finish(run)
        return run

    def resume(self, run_id: str, *, created_by: str = "cli") -> WorkflowRun:
        """Retoma um run pausado (tipicamente em aprovação humana)."""

        run = self.runtime.runs.get(run_id)
        if run is None:
            raise KeyError(f"run {run_id} not found")
        if run.finished:
            return run

        # passos em espera voltam a pendente: a aprovação já foi decidida fora
        for step in run.steps:
            if step.status in (StepRunStatus.WAITING, StepRunStatus.RUNNING):
                step.status = StepRunStatus.PENDING
                step.error = None
        self.runtime.runs.save(run)
        self.runtime.audit.record(
            EventType.TASK_RESUMED,
            actor=created_by,
            environment=str(run.environment),
            payload={"action": "workflow_resumed", "run": run.id},
        )
        return self.execute(run)

    def cancel(self, run_id: str, reason: str = "cancelled by operator", *, actor: str = "cli") -> WorkflowRun:
        run = self.runtime.runs.get(run_id)
        if run is None:
            raise KeyError(f"run {run_id} not found")
        run.status = RunStatus.CANCELLED
        run.error = reason
        run.finished_at = utcnow()
        for step in run.steps:
            if not step.terminal:
                step.status = StepRunStatus.CANCELLED
            if step.task_id:
                task = self.runtime.tasks.get(step.task_id)
                if task and not task.is_terminal:
                    self.runtime.task_engine.cancel(task.id, reason=reason)
        self.runtime.runs.save(run)
        self.runtime.audit.record(
            EventType.TASK_CANCELLED,
            actor=actor,
            environment=str(run.environment),
            payload={"action": "workflow_cancelled", "run": run.id, "reason": reason},
        )
        return run

    # ---- execução -----------------------------------------------------
    def _run_level(
        self, workflow: Workflow, run: WorkflowRun, steps: list[WorkflowStep]
    ) -> list[StepRunStatus]:
        if workflow.parallel and len(steps) > 1:
            workers = max(1, min(workflow.max_parallel, len(steps)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                return list(pool.map(lambda step: self._run_step(workflow, run, step), steps))
        return [self._run_step(workflow, run, step) for step in steps]

    def _run_step(self, workflow: Workflow, run: WorkflowRun, step: WorkflowStep) -> StepRunStatus:
        record = run.ensure_step(step.id)
        record.status = StepRunStatus.RUNNING
        record.started_at = record.started_at or utcnow()

        context = self._context(run)
        if step.condition and not self._condition_ok(step.condition, context):
            record.status = StepRunStatus.SKIPPED
            record.finished_at = utcnow()
            self.runtime.runs.save(run)
            return record.status

        attempts = max(1, step.max_attempts)
        last_error: str | None = None
        for attempt in range(1, attempts + 1):
            record.attempts = attempt
            task = self.runtime.task_engine.create(
                objective=self._objective(step, context),
                agent_id=step.agent,
                environment=step.environment or str(run.environment),
                created_by=run.created_by,
                workflow_id=workflow.id,
                workflow_run_id=run.id,
                step_id=step.id,
            )
            record.task_id = task.id
            self.runtime.runs.save(run)

            task = self.runtime.agent_engine.run(task)
            if step.namespace:
                self._remember(workflow, run, step, task)
            self._collect(run, step, task)

            if task.status == TaskStatus.COMPLETED:
                record.status = StepRunStatus.COMPLETED
                record.error = None
                record.finished_at = utcnow()
                self.runtime.runs.save(run)
                return record.status

            if task.status == TaskStatus.REQUIRES_APPROVAL:
                record.status = StepRunStatus.WAITING
                record.error = "aguardando aprovação humana"
                self.runtime.runs.save(run)
                return record.status

            last_error = task.error or f"task {task.id} terminou como {task.status}"
            record.error = last_error

        record.status = StepRunStatus.FAILED
        record.error = last_error
        record.finished_at = utcnow()
        self.runtime.runs.save(run)
        self._compensate(workflow, run, step)
        return record.status

    def _compensate(self, workflow: Workflow, run: WorkflowRun, step: WorkflowStep) -> None:
        """Executa o passo de compensação declarado, se houver."""

        policy = workflow.on_error_for(step)
        if policy != "compensate" or not step.compensate_with:
            return
        compensator = workflow.step(step.compensate_with)
        if compensator is None:
            return
        self.runtime.audit.record(
            EventType.SYSTEM_EVENT,
            actor="runtime",
            environment=str(run.environment),
            payload={
                "action": "workflow_compensation",
                "run": run.id,
                "failed_step": step.id,
                "compensation_step": compensator.id,
            },
        )
        self._run_step(workflow, run, compensator)

    def _classify_failures(
        self, workflow: Workflow, run: WorkflowRun, failed: list[WorkflowStep]
    ) -> tuple[list[WorkflowStep], list[WorkflowStep]]:
        tolerated, blocking = [], []
        for step in failed:
            (tolerated if workflow.on_error_for(step) in ("continue", "compensate") else blocking).append(step)
        return tolerated, blocking

    def _skip_blocked(self, workflow: Workflow, run: WorkflowRun, level: list[WorkflowStep]) -> None:
        """Dependência não concluída (falha tolerada ou pulada) => passo pulado.

        Executar a jusante de uma dependência que não terminou bem é inventar
        dado: o Runtime prefere marcar `skipped` e deixar o porquê registrado.
        """

        changed = False
        for step in level:
            record = run.ensure_step(step.id)
            if record.terminal:
                continue
            for dependency in step.depends_on:
                dependency_record = run.step(dependency)
                if dependency_record is None or dependency_record.status != StepRunStatus.COMPLETED:
                    record.status = StepRunStatus.SKIPPED
                    record.error = f"dependência '{dependency}' não foi concluída"
                    record.finished_at = utcnow()
                    changed = True
                    break
        if changed:
            self.runtime.runs.save(run)

    def _finish(self, run: WorkflowRun) -> None:
        summary = run.summary
        if summary["failed"]:
            run.status = RunStatus.PARTIAL
            run.error = f"{summary['failed']} passo(s) falharam (política de tolerância)"
        else:
            run.status = RunStatus.COMPLETED
        run.finished_at = utcnow()
        self.runtime.runs.save(run)
        self._record_finish(run)

    def _record_finish(self, run: WorkflowRun) -> None:
        self.runtime.audit.record(
            EventType.SYSTEM_EVENT,
            actor="runtime",
            environment=str(run.environment),
            payload={
                "action": "workflow_finished",
                "run": run.id,
                "workflow": run.workflow_id,
                "status": str(run.status),
                "steps": run.summary,
                "cost": round(run.cost, 6),
                "error": run.error,
            },
        )

    # ---- helpers ------------------------------------------------------
    def _require(self, workflow_id: str) -> Workflow:
        workflow = self.runtime.workflows.get(workflow_id)
        if workflow is None:
            self.runtime._load_workflows()
            workflow = self.runtime.workflows.get(workflow_id)
        if workflow is None:
            raise ConfigError(f"workflow '{workflow_id}' não encontrado")
        return workflow

    def _context(self, run: WorkflowRun) -> dict[str, Any]:
        return {
            "inputs": run.inputs,
            "steps": run.context,
            "run": {"id": run.id, "workflow": run.workflow_id, "environment": str(run.environment)},
        }

    def _objective(self, step: WorkflowStep, context: dict) -> str:
        raw = step.objective or (STEP_TEMPLATE.format(tool=step.tool, args=step.args) if step.tool else "")
        return str(render(raw, context))

    def _condition_ok(self, expression: str, context: dict) -> bool:
        try:
            return bool(Condition(expression).evaluate(context))
        except ConditionError:
            # condição quebrada não autoriza execução: pula e deixa rastro
            return False

    def _collect(self, run: WorkflowRun, step: WorkflowStep, task) -> None:
        """Publica o resultado do passo no contexto (base do {{steps.x.y}})."""

        result = task.result
        entry = {
            "status": str(task.status),
            "task_id": task.id,
            "answer": (result.answer if result else "") or "",
            "artifacts": list(result.artifacts) if result else [],
            "steps": len(result.steps) if result else 0,
            "cost": result.total_cost if result else 0.0,
            "error": task.error,
            "outputs": {},
        }
        # `{{task.*}}` enxerga o próprio passo; `{{steps.s1.*}}`, os anteriores
        outputs_context = {**self._context(run), "task": entry}
        entry["outputs"] = render(dict(step.outputs), outputs_context) if step.outputs else {}
        run.context[step.id] = entry
        run.cost = round(run.cost + (result.total_cost if result else 0.0), 8)
        record = run.ensure_step(step.id)
        record.outputs = entry["outputs"]
        self.runtime.runs.save(run)

    def _remember(self, workflow: Workflow, run: WorkflowRun, step: WorkflowStep, task) -> None:
        if not task.result or not task.result.answer:
            return
        self.runtime.memory.write(
            content=task.result.answer,
            kind="operational",
            namespace=step.namespace,
            summary=f"[{workflow.id}/{step.id}] {task.objective[:120]}",
            tags=["workflow", workflow.id, step.id],
            source="workflow",
            task_id=task.id,
            environment=str(run.environment),
        )


__all__ = ["WorkflowEngine"]
