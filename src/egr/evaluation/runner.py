"""O motor de avaliação: executa uma suíte e produz um veredito com números.

Cada caso vira um resultado medido (acerto, custo, latência). Cada resultado é
comparado com as expectativas declaradas — expressões seguras avaliadas contra o
contexto da execução, sem `eval` — e com os limites da suíte. Se houver baseline,
a execução é comparada com ela e o veredito pode ser `regressed`.

O que cada alvo significa:

| alvo | o caso executa | contexto das expectativas |
|---|---|---|
| `tool` | a ferramenta, isolada no sandbox | `ok`, `output`, `error`, `cost`, `duration_ms`, `files` |
| `workflow` | um run real ( cada passo é uma task ) | `status`, `steps`, `outputs`, `cost`, `duration_ms` |
| `agent` | uma task com o agente | `status`, `answer`, `steps`, `tools`, `cost` |
| `policy` | uma decisão do Policy Engine | `decision`, `allowed`, `needs_approval`, `reason`, `rule_id` |

Expressões usam a mesma gramática segura das condições de política:
`output['linhas'] == 2`, `contains(answer, 'nota')`, `cost <= 0.01`,
`decision == 'require_approval'`.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.errors import ConditionError, ConfigError
from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..dev.harness import DEFAULT_TIMEOUT, run_trial
from ..dev.loader import find_tool_source
from ..domain.enums import Environment, EvaluationStatus, EvaluationTarget, EventType
from ..domain.evaluation import (
    CaseResult,
    CheckResult,
    EvaluationCase,
    EvaluationRun,
    EvaluationSuite,
)
from ..policies.conditions import Condition
from .metrics import aggregate, compare, verdict
from .security import scan


class EvaluationRunner:
    """Roda suítes contra artefatos do workspace, com baseline e limites."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    # ---- execução -----------------------------------------------------
    def run(
        self,
        suite: EvaluationSuite,
        *,
        baseline: str | None = None,
        actor: str = "cli",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> EvaluationRun:
        if not suite.cases:
            raise ConfigError(f"suíte '{suite.id}' não tem casos")

        run = EvaluationRun(
            id=new_id("evaluation"),
            suite_id=suite.id,
            suite_version=suite.version,
            suite_fingerprint=suite.fingerprint,
            target_kind=suite.target_kind,
            target=suite.target,
            environment=suite.environment,
            thresholds=suite.thresholds,
            created_by=actor,
        )
        self.runtime.audit.record(
            EventType.EVAL_RUN_STARTED,
            actor=actor,
            environment=suite.environment,
            payload={
                "run": run.id,
                "suite": suite.id,
                "target": f"{suite.target_kind}:{suite.target}",
                "cases": len(suite.cases),
            },
        )

        started = time.perf_counter()
        run.cases = [self._run_case(suite, case, timeout=timeout) for case in suite.cases]
        run.metrics = aggregate(run.cases)
        run.findings = scan(self.runtime, str(suite.target_kind), suite.target)
        run.artifact_version = self._artifact_version(suite)

        baseline_run = self._resolve_baseline(suite, baseline)
        if baseline_run is not None:
            run.baseline_run = baseline_run.id
            run.comparison = compare(run, baseline_run, suite.thresholds)

        status, reasons = verdict(run, comparison=run.comparison, thresholds=suite.thresholds)
        if run.cases and all(case.metadata.get("fatal") for case in run.cases):
            status = str(EvaluationStatus.ERROR)
            reasons = [f"não foi possível avaliar: {run.cases[0].error}"]
        run.status = EvaluationStatus(status)
        run.reasons = reasons
        run.finished_at = utcnow()
        run.duration_ms = int((time.perf_counter() - started) * 1000)

        saved = self.runtime.evaluations.save(run)
        self.runtime.audit.record(
            EventType.EVAL_RUN_FINISHED,
            actor=actor,
            environment=suite.environment,
            payload={
                "run": saved.id,
                "status": str(saved.status),
                "pass_rate": round(saved.pass_rate, 4),
                "cost": saved.metrics.get("total_cost"),
                "p95_ms": saved.metrics.get("p95_duration_ms"),
                "baseline": saved.baseline_run,
                "regressions": saved.comparison.regressions if saved.comparison else 0,
                "findings": [f"{item.code}:{item.severity}" for item in saved.findings],
                "reasons": saved.reasons[:5],
            },
        )
        if saved.status == EvaluationStatus.REGRESSED:
            self.runtime.audit.record(
                EventType.EVAL_REGRESSION,
                actor="runtime",
                environment=suite.environment,
                payload={
                    "run": saved.id,
                    "baseline": saved.baseline_run,
                    "notes": saved.comparison.notes if saved.comparison else [],
                },
            )
        for finding in saved.findings:
            if finding.blocking:
                self.runtime.audit.record(
                    EventType.EVAL_SECURITY_FINDING,
                    actor="runtime",
                    environment=suite.environment,
                    payload={"run": saved.id, "code": finding.code, "detail": finding.detail},
                )
        return saved

    # ---- casos --------------------------------------------------------
    def _run_case(self, suite: EvaluationSuite, case: EvaluationCase, *, timeout: int) -> CaseResult:
        started = time.perf_counter()
        kind = str(suite.target_kind)
        try:
            if kind == EvaluationTarget.TOOL:
                context = self._case_tool(suite, case, timeout=timeout)
            elif kind == EvaluationTarget.WORKFLOW:
                context = self._case_workflow(suite, case)
            elif kind == EvaluationTarget.AGENT:
                context = self._case_agent(suite, case)
            elif kind == EvaluationTarget.POLICY:
                context = self._case_policy(suite, case)
            else:
                context = {"ok": False, "error": f"alvo desconhecido: {kind}", "fatal": True}
        except Exception as exc:  # falha de infraestrutura não derruba a suíte
            context = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "output": None, "fatal": True}
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)

        context.setdefault("duration_ms", elapsed)
        checks = self._check(case, context)
        output = context.get("output")
        # erro do artefato é resultado (pode até ser o esperado); exceção de
        # infraestrutura é falha da avaliação, não do caso.
        ok = all(check.ok for check in checks) and not context.get("fatal")

        return CaseResult(
            case_id=case.id,
            name=case.name or case.id,
            ok=ok,
            error=context.get("error"),
            output=output,
            duration_ms=int(context.get("duration_ms") or elapsed),
            cost=float(context.get("cost") or 0.0),
            checks=checks,
            files=list(context.get("files") or []),
            metadata={
                key: value
                for key, value in context.items()
                if key in ("status", "decision", "rule_id", "fatal")
            },
        )

    def _check(self, case: EvaluationCase, context: dict[str, Any]) -> list[CheckResult]:
        checks: list[CheckResult] = []

        if case.expect_ok is not None:
            actual = bool(context.get("ok"))
            checks.append(
                CheckResult(
                    expression=f"ok == {str(case.expect_ok).lower()}",
                    ok=actual == case.expect_ok,
                    detail=f"ok={actual}",
                )
            )

        for expression in case.expect:
            try:
                value = bool(Condition(expression).evaluate(context))
                detail = "verdadeiro" if value else "falso"
            except ConditionError as exc:
                value = False
                detail = f"expressão inválida: {exc}"
            except Exception as exc:  # pragma: no cover - defensivo
                value = False
                detail = f"falha ao avaliar: {type(exc).__name__}: {exc}"
            checks.append(CheckResult(expression=expression, ok=value, detail=detail))

        if case.max_cost is not None:
            cost = float(context.get("cost") or 0.0)
            checks.append(
                CheckResult(expression=f"cost <= {case.max_cost}", ok=cost <= case.max_cost, detail=f"cost={cost}")
            )
        if case.max_duration_ms is not None:
            duration = int(context.get("duration_ms") or 0)
            checks.append(
                CheckResult(
                    expression=f"duration_ms <= {case.max_duration_ms}",
                    ok=duration <= case.max_duration_ms,
                    detail=f"duration_ms={duration}",
                )
            )
        return checks

    # ---- alvos --------------------------------------------------------
    def _case_tool(self, suite: EvaluationSuite, case: EvaluationCase, *, timeout: int) -> dict[str, Any]:
        name = suite.target
        if not self.runtime.tools.has(name):
            return {"ok": False, "error": f"ferramenta não registrada: {name}", "output": None, "fatal": True}

        found = find_tool_source(self.runtime.settings.workspace, name)
        if found is not None:
            _, source = found
            report = run_trial(
                content=source,
                tool_name=name,
                args=case.args,
                timeout=timeout,
                container=self.runtime.sandbox_info.get("mode") == "container",
            )
            return {
                "ok": report.ok,
                "output": report.output,
                "error": None if report.ok else (report.error or "a execução falhou"),
                "cost": report.cost,
                "duration_ms": report.duration_ms,
                "files": report.files,
                "mode": report.mode,
            }

        # ferramenta da plataforma: executa no processo, sob o ctx do Runtime
        context = self.runtime.adhoc_tool_context(environment=suite.environment)
        context.dry_run = suite.dry_run
        result = self.runtime.tools.execute(name, case.args, context)
        return {
            "ok": result.ok,
            "output": result.output,
            "error": result.error,
            "cost": float(result.cost or 0.0),
            "duration_ms": result.duration_ms,
            "files": [],
            "mode": "in-process",
        }

    def _case_workflow(self, suite: EvaluationSuite, case: EvaluationCase) -> dict[str, Any]:
        workflow_id = suite.target
        if workflow_id not in self.runtime.workflows:
            return {"ok": False, "error": f"workflow não encontrado: {workflow_id}", "output": None, "fatal": True}

        run = self.runtime.orchestrator.start(
            workflow_id,
            inputs=case.args,
            environment=str(suite.environment),
            created_by=f"eval:{suite.id}",
            trigger="evaluation",
            trigger_detail=f"case:{case.id}",
        )
        duration = 0
        if run.started_at and run.finished_at:
            duration = int((run.finished_at - run.started_at).total_seconds() * 1000)
        steps = {step.id: str(step.status) for step in run.steps}
        outputs = {
            step_id: payload.get("answer")
            for step_id, payload in run.context.items()
            if isinstance(payload, dict) and payload.get("answer")
        }
        return {
            "ok": str(run.status) == "completed",
            "status": str(run.status),
            "steps": steps,
            "outputs": outputs,
            "cost": float(run.cost or 0.0),
            "duration_ms": duration,
            "error": run.error,
            "output": {"status": str(run.status), "steps": steps, "outputs": outputs},
            "run": run.id,
        }

    def _case_agent(self, suite: EvaluationSuite, case: EvaluationCase) -> dict[str, Any]:
        agent_id = suite.target
        if agent_id not in self.runtime.agents:
            return {"ok": False, "error": f"agente não encontrado: {agent_id}", "output": None, "fatal": True}

        objective = case.args.get("objective") or case.args.get("objetivo") or case.description
        if not objective:
            return {"ok": False, "error": "caso sem 'objective' para a task", "output": None, "fatal": True}

        started = time.perf_counter()
        task = self.runtime.submit(
            objective,
            agent_id=agent_id,
            environment=Environment(str(suite.environment)),
            created_by=f"eval:{suite.id}",
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        result = task.result
        answer = result.answer if result else ""
        return {
            "ok": str(task.status) == "completed",
            "status": str(task.status),
            "answer": answer,
            "steps": len(result.steps) if result else 0,
            "tools": [step.tool for step in (result.steps if result else [])],
            "cost": float((result.cost if result else 0.0) or 0.0),
            "duration_ms": result.duration_ms if result and result.duration_ms else elapsed,
            "error": task.error or (result.error if result else None),
            "output": {"answer": answer, "status": str(task.status), "task": task.id},
            "task": task.id,
        }

    def _case_policy(self, suite: EvaluationSuite, case: EvaluationCase) -> dict[str, Any]:
        from ..domain.tool import ToolRequest

        args = dict(case.args)
        tool = args.pop("tool", None) or suite.target
        agent_id = args.pop("agent_id", None)
        environment = args.pop("environment", str(suite.environment))
        action = args.pop("action", None)

        agent = self.runtime.agents.get(agent_id) if agent_id else None
        request = ToolRequest(
            tool=tool,
            args=args,
            action=action,
            agent_id=agent_id,
            environment=Environment(str(environment)),
        )
        decision = self.runtime.authorize(request, agent)
        return {
            "ok": str(decision.decision) == "allow",
            "decision": str(decision.decision),
            "allowed": bool(decision.allowed),
            "needs_approval": bool(decision.needs_approval),
            "reason": decision.reason,
            "rule_id": decision.rule_id,
            "policy_id": decision.policy_id,
            "required_role": decision.required_role,
            "output": {"decision": str(decision.decision), "reason": decision.reason},
        }

    # ---- baseline -----------------------------------------------------
    def _resolve_baseline(self, suite: EvaluationSuite, explicit: str | None):
        if explicit:
            baseline = self.runtime.evaluations.get(explicit)
            if baseline is None:
                raise ConfigError(f"baseline não encontrada: {explicit}")
            return baseline
        candidates = [
            run
            for run in self.runtime.evaluations.list(suite_id=suite.id, limit=10)
            if run.status == EvaluationStatus.PASSED
        ]
        return candidates[0] if candidates else None

    def _artifact_version(self, suite: EvaluationSuite) -> str:
        if str(suite.target_kind) == EvaluationTarget.TOOL and self.runtime.tools.has(suite.target):
            return getattr(self.runtime.tools.get(suite.target).spec, "name", suite.target)
        if str(suite.target_kind) == EvaluationTarget.WORKFLOW and suite.target in self.runtime.workflows:
            return self.runtime.workflows[suite.target].version
        if str(suite.target_kind) == EvaluationTarget.AGENT and suite.target in self.runtime.agents:
            return self.runtime.agents[suite.target].version
        return ""


__all__ = ["EvaluationRunner"]
