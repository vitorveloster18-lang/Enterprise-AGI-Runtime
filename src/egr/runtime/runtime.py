"""The Runtime: wires every layer together.

    CLI/API -> Task -> Agent -> (Memory + Model) -> Plan -> Policy -> Tool -> Audit
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..audit import AuditLedger
from ..core.config import Settings, load_settings
from ..core.ids import new_id
from ..core.logging import get_logger, setup_logging
from ..core.paths import find_workspace_root, require_workspace_root
from ..core.timeutil import utcnow
from ..domain.agent import AgentPermissions, AgentSpec, ModelSpec
from ..domain.approval import Approval
from ..domain.enums import (
    ApprovalStatus,
    Environment,
    EventType,
    RiskLevel,
    TaskStatus,
)
from ..domain.task import StepRecord, Task, TaskResult
from ..domain.tool import ToolRequest
from ..memory import MemoryService
from ..models import ModelGateway
from ..models.gateway import CompletionRequest, Message, build_providers
from ..policies import PolicyEngine, default_policies
from ..policies.engine import PolicyContext
from ..security.redaction import redact_mapping
from ..storage import Database, apply_migrations, migration_status
from ..storage.repositories import (
    AgentRepository,
    ApprovalRepository,
    ArtifactRepository,
    EnterpriseRepository,
    MemoryRepository,
    ModelUsageRepository,
    PolicyRepository,
    SettingsRepository,
    TaskRepository,
)
from ..tools import ToolRegistry, register_builtin_tools
from ..tools.protocol import ToolContext
from .agent_engine import AgentEngine
from .events import EventBus
from .loader import load_agent_dir, load_workflow_dir
from .task_engine import TaskEngine

LOGGER = get_logger("egr.runtime")


def default_agents() -> list[AgentSpec]:
    """Seeded on `egr init`; source of truth afterwards is agents/*.yaml + DB."""

    return [
        AgentSpec(
            id="runtime-agent",
            name="Runtime Agent",
            objective="Executar trabalho operacional usando ferramentas locais do Runtime",
            model=ModelSpec(capability="reasoning"),
            memory=["default"],
            permissions=AgentPermissions(
                tools=["filesystem.*", "python.execute", "database.query"], namespaces=["default"]
            ),
            environment=Environment.DEVELOPMENT,
        ),
        AgentSpec(
            id="document-agent",
            name="Document Agent",
            objective="Analisar documentos locais, classificar e produzir relatorios auditaveis",
            model=ModelSpec(capability="reasoning"),
            memory=["documents", "default"],
            permissions=AgentPermissions(
                tools=["filesystem.*", "python.execute"], namespaces=["documents", "default"]
            ),
            environment=Environment.DEVELOPMENT,
        ),
        AgentSpec(
            id="finance-agent",
            name="Finance Agent",
            objective="Executar operacoes financeiras com aprovacao humana acima do limite",
            model=ModelSpec(capability="reasoning"),
            memory=["finance"],
            permissions=AgentPermissions(
                tools=["filesystem.*", "database.query"], namespaces=["finance"]
            ),
            environment=Environment.DEVELOPMENT,
        ),
    ]


class Runtime:
    """Everything the Runtime owns. Stateless across processes: state lives in the DB."""

    def __init__(self, settings: Settings, *, enable_logging: bool = True):
        self.settings = settings
        settings.ensure_dirs()

        if enable_logging:
            log_file = settings.logs_path / "egr.jsonl" if settings.config.logging.file else None
            setup_logging(settings.config.logging.level, log_file)

        # ---- infrastructure -----------------------------------------
        self.db = Database(settings.db_path)
        self.applied_migrations = apply_migrations(self.db)
        self.migration_state = migration_status(self.db)

        # ---- governance ---------------------------------------------
        self.audit = AuditLedger(self.db)
        self.events = EventBus(self.audit)

        # ---- repositories -------------------------------------------
        self.enterprises = EnterpriseRepository(self.db)
        self.agent_repository = AgentRepository(self.db)
        self.tasks = TaskRepository(self.db)
        self.policy_repository = PolicyRepository(self.db)
        self.approvals = ApprovalRepository(self.db)
        self.artifacts = ArtifactRepository(self.db)
        self.settings_repository = SettingsRepository(self.db)
        self.usage = ModelUsageRepository(self.db)
        self.memory = MemoryService(MemoryRepository(self.db), self.audit)

        # ---- intelligence -------------------------------------------
        self.policy = PolicyEngine()
        self.policy.set_policies([*default_policies(), *self.policy_repository.list(enabled_only=False)])
        self.gateway = ModelGateway(
            build_providers(
                settings.config.models.providers,
                timeout=settings.config.runtime.model_timeout,
            ),
            audit=self.audit,
            external_ai=settings.config.enterprise.settings.external_ai,
            sanitize_external=settings.config.security.sanitize_external_payloads,
            timeout=settings.config.runtime.model_timeout,
            usage_repository=self.usage,
            budget=settings.config.models.budget,
            routing=settings.config.models.routing,
        )

        # ---- execution ----------------------------------------------
        self.tools = register_builtin_tools(ToolRegistry())
        self.agents: dict[str, AgentSpec] = {}
        self.agent_engine = AgentEngine(self)
        self.task_engine = TaskEngine(self)

        # ---- bootstrap ----------------------------------------------
        self._ensure_enterprise()
        self._load_agents()
        self._load_workflows()

    # ---- construction -----------------------------------------------
    @classmethod
    def load(cls, workspace: Path | str | None = None, environment: str | None = None) -> Runtime:
        root = Path(workspace).resolve() if workspace else require_workspace_root()
        settings = load_settings(root, environment=environment)
        return cls(settings)

    @classmethod
    def exists(cls, workspace: Path | str | None = None) -> bool:
        return find_workspace_root(workspace) is not None

    # ---- bootstrap internals ----------------------------------------
    def _ensure_enterprise(self) -> None:
        stored = self.enterprises.first()
        if stored is None:
            self.enterprises.save(self.settings.config.enterprise)
        else:
            self.settings.config.enterprise = stored

    def _load_agents(self) -> None:
        stored = self.agent_repository.list()
        if not stored:
            for agent in default_agents():
                self.agent_repository.save(agent)
            stored = self.agent_repository.list()
        self.agents = {agent.id: agent for agent in stored}

    def _load_workflows(self) -> None:
        self.workflows = {
            workflow.id: workflow for workflow in load_workflow_dir(self.settings.workspace / "workflows")
        }

    def reload_agents(self) -> None:
        self._load_agents()

    # ---- declarative sync (YAML -> runtime) --------------------------
    def sync_agents(self) -> list[AgentSpec]:
        """Load agents/*.yaml into the runtime (source of truth = repository)."""

        specs = load_agent_dir(self.settings.workspace / "agents")
        for spec in specs:
            self.register_agent(spec)
        return specs

    def sync_policies(self) -> list:
        """Load policies/*.yaml into the runtime."""

        from ..policies.loader import load_policy_dir

        policies = load_policy_dir(self.settings.workspace / "policies")
        for policy in policies:
            self.policy_repository.save(policy)
        self.policy.set_policies([*default_policies(), *self.policy_repository.list(enabled_only=False)])
        self.audit.record(
            EventType.SYSTEM_EVENT,
            actor="runtime",
            payload={"action": "sync_policies", "count": len(policies)},
        )
        return policies

    def sync_all(self) -> dict[str, int]:
        agents = self.sync_agents()
        policies = self.sync_policies()
        self._load_workflows()
        return {"agents": len(agents), "policies": len(policies), "workflows": len(self.workflows)}

    def register_agent(self, agent: AgentSpec) -> AgentSpec:
        self.agent_repository.save(agent)
        self.agents[agent.id] = agent
        self.audit.record(
            EventType.AGENT_LOADED,
            actor="runtime",
            agent_id=agent.id,
            payload={"agent": agent.id, "version": agent.version, "action": "registered"},
        )
        return agent

    # ---- accessors ---------------------------------------------------
    def resolve_agent(self, agent_id: str | None) -> AgentSpec:
        if agent_id and agent_id in self.agents:
            return self.agents[agent_id]
        if agent_id:
            stored = self.agent_repository.get(agent_id)
            if stored:
                self.agents[stored.id] = stored
                return stored
            raise KeyError(f"unknown agent '{agent_id}'")
        return self.default_agent()

    def default_agent(self) -> AgentSpec:
        if "runtime-agent" in self.agents:
            return self.agents["runtime-agent"]
        if self.agents:
            return next(iter(self.agents.values()))
        agent = default_agents()[0]
        self.register_agent(agent)
        return agent

    def new_result(self) -> TaskResult:
        return TaskResult()

    # ---- contexts ----------------------------------------------------
    @property
    def security_context(self) -> dict[str, Any]:
        return self.settings.config.security.model_dump()

    def tool_context(self, task: Task, agent: AgentSpec | None = None) -> ToolContext:
        return ToolContext(
            workspace=self.settings.workspace,
            sandbox=self.settings.sandbox_path,
            artifacts=self.settings.artifacts_path,
            environment=task.environment,
            enterprise_id=self.settings.enterprise.id,
            task_id=task.id,
            agent_id=agent.id if agent else task.agent_id,
            timeout=self.settings.config.runtime.tool_timeout,
            security=self.security_context,
            database_path=self.settings.db_path,
        )

    def adhoc_tool_context(
        self,
        environment: Environment | str | None = None,
        task_id: str | None = None,
        agent: AgentSpec | None = None,
    ) -> ToolContext:
        """Tool context for actions executed outside a task (CLI, API, tests)."""

        return ToolContext(
            workspace=self.settings.workspace,
            sandbox=self.settings.sandbox_path,
            artifacts=self.settings.artifacts_path,
            environment=Environment(environment or self.settings.environment),
            enterprise_id=self.settings.enterprise.id,
            task_id=task_id,
            agent_id=agent.id if agent else None,
            timeout=self.settings.config.runtime.tool_timeout,
            security=self.security_context,
            database_path=self.settings.db_path,
        )

    def completion_request(self, messages: list[Message], agent: AgentSpec) -> CompletionRequest:
        return CompletionRequest(
            messages=messages,
            capability=agent.model.capability,
            temperature=agent.model.temperature,
            max_tokens=agent.model.max_tokens,
            json_mode=True,
            metadata={"agent": agent.id, "capability": agent.model.capability},
        )

    # ---- governance ---------------------------------------------------
    def authorize(self, request: ToolRequest, agent: AgentSpec | None = None):
        risk = RiskLevel.LOW
        if self.tools.has(request.tool):
            risk = self.tools.get(request.tool).spec.risk
        context = PolicyContext(
            environment=request.environment,
            agent=agent,
            enterprise_settings=self.settings.enterprise.settings.model_dump(),
            risk=risk,
            security=self.security_context,
        )
        return self.policy.evaluate(request, context)

    def request_action(
        self,
        tool: str,
        args: dict | None = None,
        *,
        agent_id: str | None = None,
        environment: Environment | str | None = None,
        action: str | None = None,
        rationale: str = "",
    ):
        """Direct, governed action (used by `egr tool test` and the API)."""

        agent = self.resolve_agent(agent_id) if agent_id else None
        request = ToolRequest(
            tool=tool,
            args=args or {},
            action=action,
            agent_id=agent.id if agent else None,
            environment=Environment(environment or self.settings.environment),
            rationale=rationale,
        )
        return request, self.authorize(request, agent)

    # ---- approvals ----------------------------------------------------
    def request_approval(
        self,
        request: ToolRequest,
        decision,
        agent: AgentSpec | None = None,
        task_id: str | None = None,
        step_id: str = "adhoc",
        environment: Environment | str | None = None,
    ) -> Approval:
        """Create a first-class Approval object (human in the loop)."""

        approval = Approval(
            id=new_id("approval"),
            action=request.policy_action,
            tool=request.tool,
            args=redact_mapping(request.args),
            requested_by=agent.id if agent else "cli",
            task_id=task_id,
            step_id=step_id,
            environment=Environment(environment or self.settings.environment),
            required_role=decision.required_role or "operator",
            reason=decision.reason,
        )
        self.approvals.save(approval)
        self.audit.record(
            EventType.APPROVAL_REQUESTED,
            actor=agent.id if agent else "cli",
            task_id=task_id,
            agent_id=agent.id if agent else None,
            environment=str(approval.environment),
            payload={
                "approval": approval.id,
                "tool": approval.tool,
                "required_role": approval.required_role,
                "reason": approval.reason,
                "step": step_id,
            },
        )
        return approval

    def approve(self, approval_id: str, decided_by: str = "human", note: str | None = None) -> Task:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise KeyError(f"approval {approval_id} not found")
        approval.status = ApprovalStatus.APPROVED
        approval.decided_by = decided_by
        approval.decided_at = approval.decided_at or utcnow()
        approval.decision_note = note
        self.approvals.save(approval)
        self.audit.record(
            EventType.APPROVAL_DECIDED,
            actor=decided_by,
            task_id=approval.task_id,
            environment=str(approval.environment),
            payload={"approval": approval.id, "status": "approved", "note": note},
        )
        self.audit.record(
            EventType.HUMAN_DECISION,
            actor=decided_by,
            task_id=approval.task_id,
            environment=str(approval.environment),
            payload={"approval": approval.id, "decision": "approved", "tool": approval.tool},
        )
        task = self.tasks.get(approval.task_id) if approval.task_id else None
        if task is not None:
            return self.agent_engine.resume_after_approval(task, approval)
        return task

    def deny(self, approval_id: str, decided_by: str = "human", note: str | None = None) -> Task | None:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise KeyError(f"approval {approval_id} not found")
        approval.status = ApprovalStatus.DENIED
        approval.decided_by = decided_by
        approval.decision_note = note
        self.approvals.save(approval)
        self.audit.record(
            EventType.APPROVAL_DECIDED,
            actor=decided_by,
            task_id=approval.task_id,
            environment=str(approval.environment),
            payload={"approval": approval.id, "status": "denied", "note": note},
        )
        self.audit.record(
            EventType.HUMAN_DECISION,
            actor=decided_by,
            task_id=approval.task_id,
            environment=str(approval.environment),
            payload={"approval": approval.id, "decision": "denied", "tool": approval.tool},
        )
        task = self.tasks.get(approval.task_id) if approval.task_id else None
        if task is None:
            return None
        step_id = task.context.get("pending_step")
        if task.result is not None and step_id:
            task.result.steps.append(
                StepRecord(
                    id=step_id,
                    tool=approval.tool,
                    args=approval.args,
                    decision="denied_by_human",
                    approval_id=approval.id,
                    ok=False,
                    error=note or "denied by human",
                )
            )
        task.context["cursor"] = int(task.context.get("cursor", 0)) + 1
        task.context.pop("pending_approval", None)
        task.context.pop("pending_step", None)
        task.status = TaskStatus.RUNNING
        self.tasks.save(task)
        return self.agent_engine.run(task)

    # ---- tasks --------------------------------------------------------
    def submit(
        self,
        objective: str,
        agent_id: str | None = None,
        environment: Environment | str | None = None,
        created_by: str = "cli",
    ) -> Task:
        return self.task_engine.submit(objective, agent_id=agent_id, environment=environment, created_by=created_by)

    # ---- status -------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "workspace": str(self.settings.workspace),
            "environment": str(self.settings.environment),
            "enterprise": {
                "id": self.settings.enterprise.id,
                "name": self.settings.enterprise.name,
                "settings": self.settings.enterprise.settings.model_dump(),
            },
            "database": {
                "path": str(self.settings.db_path),
                "migrations": self.migration_state,
            },
            "counts": {
                "agents": len(self.agents),
                "tasks": self.tasks.count_by_status(),
                "tools": len(self.tools.list()),
                "policies": len(self.policy.list_policies()),
                "approvals_pending": len(self.approvals.list(status="pending")),
                "artifacts": self.artifacts.count(),
                "memory": self.memory.stats(),
                "events": self.audit.count(),
            },
            "models": self.gateway.list_providers(),
            "routing": self.settings.config.models.routing,
            "budget": self.settings.config.models.budget.model_dump(),
            "spend": {
                "today": self.usage.totals_today(),
                "total": self.usage.totals(),
            },
            "tools_available": [tool["name"] for tool in self.tools.list()],
        }

    def health(self) -> dict[str, Any]:
        """Used by `egr doctor` and the API."""

        checks: list[dict[str, Any]] = []

        def add(name: str, ok: bool, detail: str = ""):
            checks.append({"check": name, "ok": bool(ok), "detail": detail})

        import sys

        add("python", sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}")
        add("workspace", self.settings.workspace.exists(), str(self.settings.workspace))
        add(
            "database",
            self.settings.db_path.exists(),
            f"{self.settings.db_path} ({self.migration_state['total']} migrations)",
        )
        add("migrations", not self.migration_state["pending"], f"pending: {self.migration_state['pending']}")
        add(
            "audit_ledger",
            self.audit.verify()["valid"],
            f"{self.audit.count()} events, chain valid={self.audit.verify()['valid']}",
        )
        add("tools", len(self.tools.list()) > 0, f"{len(self.tools.list())} tools registered")
        add("policies", len(self.policy.list_policies()) > 0, f"{len(self.policy.list_policies())} policies active")
        add("agents", len(self.agents) > 0, f"{len(self.agents)} agents loaded")
        add(
            "sandbox",
            self.settings.sandbox_path.exists(),
            f"{self.settings.sandbox_path} (python_exec={self.settings.config.security.python_exec_enabled})",
        )
        for name, report in self.gateway.health().items():
            add(f"model:{name}", report["healthy"], report["detail"])
        budget = self.settings.config.models.budget
        if budget.per_day is not None:
            spent = self.usage.totals_today()["total_cost"]
            add(
                "budget:day",
                spent < budget.per_day,
                f"{spent:.6f} / {budget.per_day:.6f} {budget.currency} (on_exceeded={budget.on_exceeded})",
            )

        return {
            "checks": checks,
            "healthy": all(check["ok"] for check in checks),
            "environment": str(self.settings.environment),
        }

    def close(self) -> None:
        self.db.close()
