"""The Runtime: wires every layer together.

    CLI/API -> Task -> Agent -> (Memory + Model) -> Plan -> Policy -> Tool -> Audit
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..audit import AuditLedger
from ..core.config import Settings, load_settings
from ..core.errors import AuthenticationError, AuthorizationError, ConfigError
from ..core.ids import new_id
from ..core.logging import get_logger, setup_logging
from ..core.paths import find_workspace_root, require_workspace_root
from ..core.timeutil import utcnow
from ..dev.loader import load_tool_dir
from ..dev.workbench import Workbench
from ..domain.agent import AgentPermissions, AgentSpec, ModelSpec
from ..domain.approval import Approval
from ..domain.enums import (
    ApprovalStatus,
    Environment,
    EventType,
    ProposalStatus,
    RiskLevel,
    TaskStatus,
)
from ..domain.evaluation import EvaluationSuite
from ..domain.proposal import ChangeProposal
from ..domain.task import StepRecord, Task, TaskResult
from ..domain.tool import ToolRequest
from ..evaluation.loader import load_suite_dir
from ..evaluation.runner import EvaluationRunner
from ..gateway import build_channels
from ..gateway.service import GatewayService
from ..integrations import ConnectorService
from ..memory import MemoryService
from ..models import ModelGateway
from ..models.gateway import CompletionRequest, Message, build_providers
from ..packs import PackService
from ..policies import PolicyEngine, default_policies
from ..policies.engine import PolicyContext
from ..release.manager import ReleaseManager
from ..security.identity import IdentityService, PrincipalKind
from ..security.keystore import MasterKey, MasterKeyStore
from ..security.rbac import APPROVAL_DECIDE, PERMISSIONS, ROLES, has_permission, role_satisfies
from ..security.redaction import redact_mapping
from ..security.vault import SecretVault
from ..storage import Database, apply_migrations, migration_status
from ..storage.repositories import (
    AgentRepository,
    ApprovalRepository,
    ArtifactRepository,
    ArtifactVersionRepository,
    ChangeProposalRepository,
    EnterpriseRepository,
    EvaluationRunRepository,
    EvaluationSuiteRepository,
    GatewayBindingRepository,
    GatewayMessageRepository,
    IdentityRepository,
    IntegrationCallRepository,
    IntegrationEventRepository,
    IntegrationJobRepository,
    IntegrationRepository,
    KeyRepository,
    MemoryRepository,
    ModelUsageRepository,
    PackRepository,
    PolicyRepository,
    ReleaseRepository,
    SecretRepository,
    SettingsRepository,
    TaskRepository,
    WorkflowRunRepository,
)
from ..tools import ToolRegistry, register_builtin_tools
from ..tools.protocol import ToolContext
from .agent_engine import AgentEngine
from .events import EventBus
from .loader import load_agent_dir, load_workflow_dir
from .scheduler import WorkflowScheduler
from .task_engine import TaskEngine
from .workflow_engine import WorkflowEngine

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
                tools=[
                    "dev.propose",
                    "dev.proposals",
                    "dev.trial",
                    "filesystem.*",
                    "python.execute",
                    "database.query",
                ],
                namespaces=["default"],
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
        self.memory = MemoryService(
            MemoryRepository(self.db),
            audit=self.audit,
            config=settings.config.memory,
        )

        # ---- segurança (Fase 4) -------------------------------------
        self.keystore = MasterKeyStore(settings.workspace)
        self.key_repository = KeyRepository(self.db)
        self.identity = IdentityService(IdentityRepository(self.db), audit=self.audit)
        self.vault = SecretVault(SecretRepository(self.db), self.keystore, audit=self.audit)

        # ---- orquestração (Fase 6) ----------------------------------
        self.runs = WorkflowRunRepository(self.db)
        self.proposals = ChangeProposalRepository(self.db)
        self.suites = EvaluationSuiteRepository(self.db)
        self.evaluations = EvaluationRunRepository(self.db)
        self.releases = ReleaseRepository(self.db)
        self.versions = ArtifactVersionRepository(self.db)
        self.gateway_bindings = GatewayBindingRepository(self.db)
        self.gateway_messages = GatewayMessageRepository(self.db)
        # Fase 11: integrações (REST/GraphQL/SQL/webhook)
        self.integrations_repository = IntegrationRepository(self.db)
        self.integration_calls = IntegrationCallRepository(self.db)
        self.integration_events = IntegrationEventRepository(self.db)
        self.integration_jobs = IntegrationJobRepository(self.db)
        # Fase 12: pacotes verticais instalados
        self.packs_repository = PackRepository(self.db)

        # ---- intelligence -------------------------------------------
        self.policy = PolicyEngine()
        self.policy.set_policies([*default_policies(), *self.policy_repository.list(enabled_only=False)])
        self.gateway = ModelGateway(
            build_providers(
                settings.config.models.providers,
                timeout=settings.config.runtime.model_timeout,
                secret_resolver=self.resolve_secret,
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
        self.tool_load_rejections = self._load_workspace_tools()
        self.mcp_failures: list[dict] = []
        self._register_mcp_tools()
        self.agents: dict[str, AgentSpec] = {}
        self.agent_engine = AgentEngine(self)
        self.task_engine = TaskEngine(self)
        self.orchestrator = WorkflowEngine(self)
        self.workbench = Workbench(self)
        self.evaluator = EvaluationRunner(self)
        self.evaluation_suites = self._load_suites()
        self.release_manager = ReleaseManager(self)
        # Fase 10: canais (Telegram/Slack/Web) — `self.gateway` continua sendo o ModelGateway
        self.channels = GatewayService(self)
        self._load_channels()
        # Fase 11: conectores declarados em `integrations/*.yaml`
        self.connectors = ConnectorService(self)
        self._load_integrations()
        # Fase 12: catálogo de pacotes verticais (instalação é proposta)
        self.packs = PackService(self)
        self.scheduler = WorkflowScheduler(self)

        # ---- bootstrap ----------------------------------------------
        self._ensure_enterprise()
        self._load_agents()
        self._load_workflows()
        self._triggers_bound = False

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

    def _load_channels(self) -> None:
        """Instancia os canais declarados e registra no gateway."""

        for channel in build_channels(self):
            self.channels.register(channel)

    def _load_integrations(self) -> None:
        """Conectores declarados entram no registro em memória."""

        self.connectors.load()

    def packs_status(self) -> dict[str, Any]:
        """Fase 12: o que a empresa já instalou e o que ainda pode instalar."""

        return self.packs.status()

    def integrations_status(self) -> dict[str, Any]:
        """Fase 11: com quem o Runtime conversa — e o que isso custou."""

        return self.connectors.status()

    def channel_status(self) -> dict[str, Any]:
        """Fase 10: por onde o Runtime conversa com gente."""

        return self.channels.status()

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

    def sync_integrations(self) -> list:
        """Load integrations/*.yaml into the runtime."""

        integrations = self.connectors.sync()
        if integrations:
            self.audit.record(
                EventType.SYSTEM_EVENT,
                actor="runtime",
                payload={"action": "sync_integrations", "count": len(integrations)},
            )
        return integrations

    def sync_all(self) -> dict[str, int]:
        agents = self.sync_agents()
        policies = self.sync_policies()
        integrations = self.sync_integrations()
        self._load_workflows()
        return {
            "agents": len(agents),
            "policies": len(policies),
            "workflows": len(self.workflows),
            "integrations": len(integrations),
        }

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
        """Tudo que uma ferramenta pode consultar para se auto-configurar."""

        tools = self.settings.config.tools
        return {
            **self.settings.config.security.model_dump(),
            "sandbox": tools.sandbox.model_dump(),
            "git_enabled": tools.git.enabled,
            "git_binary": tools.git.binary,
            "email": tools.email.model_dump(),
            "browser": tools.browser.model_dump(),
        }

    def resolve_secret(self, reference: str | None) -> str | None:
        """Resolve `vault:NOME` (cofre) ou `VARIAVEL` (ambiente).

        É o único caminho pelo qual um provedor de modelo recebe uma credencial.
        """

        from ..security.secrets import resolve_secret

        return resolve_secret(reference, vault=self.vault)

    @property
    def sandbox_info(self) -> dict[str, Any]:
        from ..tools.sandbox import SandboxRunner

        runner = SandboxRunner(
            self.settings.config.tools.sandbox,
            workspace=self.settings.workspace,
            sandbox_dir=self.settings.sandbox_path,
            artifacts_dir=self.settings.artifacts_path,
            environment=str(self.settings.environment),
        )
        return runner.describe()

    def _load_suites(self) -> dict[str, EvaluationSuite]:
        """Suítes do workspace: registradas (banco) + declaradas em `evaluations/*.yaml`.

        O YAML versionado tem a palavra final: se existir, ele sobrepõe o que
        foi registrado por CLI/API. Assim a suíte que o time revisa no
        repositório é a que vale.
        """

        suites: dict[str, EvaluationSuite] = {suite.id: suite for suite in self.suites.list()}
        for suite in load_suite_dir(self.settings.workspace / "evaluations"):
            self.suites.save(suite)
            suites[suite.id] = suite
        return suites

    def _load_workspace_tools(self) -> list[dict]:
        """Ferramentas criadas por proposta aprovada (`tools/*.py`).
        Arquivo que não passa pela verificação estática é recusado com evento.
        """

        report = load_tool_dir(
            self.settings.workspace / "tools",
            audit=self.audit,
            environment=str(self.settings.environment),
        )
        for tool in report.loaded:
            self.tools.register(tool)
        return report.rejected

    def _register_mcp_tools(self) -> None:
        """Ferramentas MCP entram como Tools — sem herdar nenhuma permissão."""

        from ..tools.mcp import connect_mcp_servers

        if not self.settings.config.mcp.servers:
            return
        proxies, failures = connect_mcp_servers(self.settings.config.mcp, workspace=self.settings.workspace)
        for proxy in proxies:
            self.tools.register(proxy)
        self.mcp_failures = failures
        self.audit.record(
            EventType.SYSTEM_EVENT,
            actor="runtime",
            payload={
                "action": "mcp_discovery",
                "tools": [proxy.spec.name for proxy in proxies],
                "failures": failures,
            },
        )

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
            runtime=self,
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
            runtime=self,
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
            required_role=decision.required_role or self.settings.config.security.approval_min_role,
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

    # ---- identidade e autorização (Fase 4) ---------------------------
    def _authorize_decision(self, approval: Approval, actor: str, token: str | None = None) -> str:
        """Decide quem pode decidir — e registra a tentativa, válida ou não.

        Retorna o id do decisor efetivo. Com `security.identity_required` ativo
        só passa um Principal autenticado, com permissão `approval.decide` e com
        papel que satisfaça o exigido pela política.
        """

        security = self.settings.config.security
        principal = self.identity.resolve(token) if token else self.identity.resolve(actor)

        def deny(reason: str) -> None:
            self.audit.record(
                EventType.AUTHORIZATION_DENIED,
                actor=actor or "anonymous",
                task_id=approval.task_id,
                environment=str(approval.environment),
                payload={
                    "approval": approval.id,
                    "tool": approval.tool,
                    "required_role": approval.required_role,
                    "reason": reason,
                },
            )
            raise AuthorizationError(reason)

        if not security.identity_required:
            # Sem exigência de identidade o Runtime continua funcionando, mas a
            # auditoria fica explicitamente marcada como não verificada.
            return principal.id if principal else (actor or "human")

        if principal is None:
            # 401: não há identidade verificável (credencial ausente, inválida,
            # expirada ou revogada). 403 fica para quem tem identidade, mas não
            # tem permissão — a distinção importa para a API e para a auditoria.
            self.audit.record(
                EventType.AUTH_FAILED,
                actor=actor or "anonymous",
                task_id=approval.task_id,
                environment=str(approval.environment),
                payload={
                    "approval": approval.id,
                    "tool": approval.tool,
                    "required_role": approval.required_role,
                    "reason": "unverified_identity",
                },
            )
            raise AuthenticationError(
                "identidade não verificada: decisão exige um principal autenticado "
                "(use `egr identity token <id>` e --token)"
            )
        if not has_permission(principal, APPROVAL_DECIDE):
            deny(f"'{principal.id}' não tem a permissão '{APPROVAL_DECIDE}'")
        if not security.allow_agent_approval and principal.kind == PrincipalKind.AGENT:
            deny(f"agente '{principal.id}' não pode aprovar o próprio trabalho")
        if not role_satisfies(principal.roles, approval.required_role):
            deny(
                f"'{principal.id}' tem papéis {sorted(principal.roles)}, "
                f"mas a política exige '{approval.required_role}'"
            )
        return principal.id

    def approve(
        self,
        approval_id: str,
        decided_by: str = "human",
        note: str | None = None,
        token: str | None = None,
    ) -> Task:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise KeyError(f"approval {approval_id} not found")
        decided_by = self._authorize_decision(approval, decided_by, token)
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
            payload={
                "approval": approval.id,
                "decision": "approved",
                "tool": approval.tool,
                "identity_verified": self.identity.resolve(decided_by) is not None,
            },
        )
        task = self.tasks.get(approval.task_id) if approval.task_id else None
        if task is not None:
            return self.agent_engine.resume_after_approval(task, approval)
        return task

    def deny(
        self,
        approval_id: str,
        decided_by: str = "human",
        note: str | None = None,
        token: str | None = None,
    ) -> Task | None:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise KeyError(f"approval {approval_id} not found")
        decided_by = self._authorize_decision(approval, decided_by, token)
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

    # ---- segurança: chaves, cofre e identidade ------------------------
    # ---- orquestração (Fase 6) ---------------------------------------
    def run_workflow(
        self,
        workflow_id: str,
        *,
        inputs: dict | None = None,
        environment: str | None = None,
        created_by: str = "cli",
        trigger: str = "manual",
        trigger_detail: str = "",
    ):
        return self.orchestrator.start(
            workflow_id,
            inputs=inputs,
            environment=environment,
            created_by=created_by,
            trigger=trigger,
            trigger_detail=trigger_detail,
        )

    def bind_triggers(self) -> int:
        """Liga eventos do ledger a workflows (idempotente)."""

        from .triggers import bind

        return bind(self)

    def orchestration_status(self) -> dict[str, Any]:
        runs = self.runs.list(limit=200)
        return {
            "workflows": len(self.workflows),
            "triggers": [
                {
                    "workflow": workflow.id,
                    "type": workflow.trigger.type,
                    "event": workflow.trigger.event,
                    "cron": workflow.trigger.cron,
                    "enabled": workflow.trigger.enabled,
                    "steps": len(workflow.steps),
                    "parallel": workflow.parallel,
                }
                for workflow in self.workflows.values()
            ],
            "runs": {
                "total": self.runs.count(),
                "by_status": self.runs.stats(),
                "recent": [run.id for run in runs[:5]],
            },
            "scheduler": {
                "due_now": [
                    item for item in self.scheduler.due() if "error" not in item
                ],
                "cron_errors": [item for item in self.scheduler.due() if "error" in item],
                "upcoming": self.scheduler.upcoming(limit=5),
            },
        }

    def governance_status(self) -> dict[str, Any]:
        """Promoção entre ambientes: releases, versões e o que está aplicado."""

        return self.release_manager.status()

    def evaluation_status(self) -> dict[str, Any]:
        """Raio-X da avaliação: suítes, últimos vereditos, custo e latência."""

        runs = self.evaluations.list(limit=200)
        last_by_suite: dict[str, dict] = {}
        for run in runs:
            last_by_suite.setdefault(run.suite_id, run.summary())
        return {
            "suites": {
                "total": len(self.evaluation_suites),
                "stored": self.suites.count(),
                "items": [suite.summary() for suite in self.evaluation_suites.values()],
            },
            "runs": {
                "total": self.evaluations.count(),
                "by_status": self.evaluations.stats(),
                "last_by_suite": last_by_suite,
                "recent": [run.summary() for run in runs[:5]],
            },
        }

    def dev_status(self) -> dict[str, Any]:
        """Raio-X do ambiente de desenvolvimento: o que foi proposto e por quem."""

        return self.workbench.status()

    # ---- desenvolvimento (Fase 7) ------------------------------------
    # ---- delegação do CLI `egr proposal` (Fase 7/8) ------------------
    def dev_propose(self, kind: str, name: str, content: str, rationale: str = "") -> ChangeProposal:
        """Cria uma proposta pelo caminho canônico: o Workbench."""

        return self.workbench.propose(kind, name, content, origin="human:cli", rationale=rationale)

    def dev_verify(self, proposal_id: str) -> ChangeProposal:
        return self.workbench.validate(proposal_id)

    def dev_prove(self, proposal_id: str, suite_id: str | None = None):
        """Avalia o artefato proposto e anexa a execução como evidência.

        Pack não tem prova em sandbox: a avaliação acontece depois de aplicar,
        sobre os artefatos (`egr eval smoke workflow <id>`).
        """

        from ..evaluation.suites import smoke_suite

        proposal = self.workbench.get(proposal_id)
        if str(proposal.kind) == "pack":
            raise ConfigError(
                "pack não tem prova em sandbox: aplique e avalie os artefatos "
                "(egr eval smoke workflow <id>) antes de promover"
            )
        suite = self.evaluation_suites.get(suite_id) if suite_id else None
        if suite is None:
            if not self._artifact_exists(proposal):
                raise ConfigError(
                    f"artefato '{proposal.name}' ainda não está no workspace: aplique a proposta "
                    "ou informe uma suíte existente (--suite)"
                )
            suite = smoke_suite(self, str(proposal.kind), proposal.name)
        run = self.evaluator.run(suite, actor=proposal.origin or "human:cli")
        proposal.metadata["evidence_run"] = run.id
        proposal.metadata["evidence_status"] = str(run.status)
        if str(run.status) == "passed":
            proposal.status = ProposalStatus.TESTED
        else:
            proposal.status = ProposalStatus.FAILED
            proposal.error = "; ".join(run.reasons[:2]) or "avaliação não passou"
        proposal.updated_at = utcnow()
        saved = self.proposals.save(proposal)
        self.audit.record(
            EventType.DEV_PROPOSAL_TESTED,
            actor=proposal.origin or "human:cli",
            environment=saved.environment,
            payload={"proposal": saved.id, "evidence": run.id, "status": str(run.status)},
        )
        return saved, run

    def _artifact_exists(self, proposal: ChangeProposal) -> bool:
        """O que já está no workspace pode ser provado; o que ainda não está, não."""

        kind, name = str(proposal.kind), proposal.name
        if kind == "tool":
            return self.tools.has(name)
        if kind == "agent":
            return name in self.agents
        if kind == "workflow":
            return name in self.workflows
        if kind == "policy":
            return any(item.id == name for item in self.policy.list_policies())
        if kind == "pack":
            return self.packs_repository.get(name) is not None
        return False

    def dev_approve(self, proposal_id: str, actor: str = "human:cli", token: str | None = None, note: str = ""):
        return self.workbench.approve(proposal_id, actor=actor, note=note)

    def dev_reject(self, proposal_id: str, actor: str = "human:cli", note: str = ""):
        return self.workbench.reject(proposal_id, actor=actor, note=note)

    def dev_apply(self, proposal_id: str, actor: str = "human:cli") -> dict[str, Any]:
        proposal = self.workbench.apply(proposal_id, actor=actor)
        return {
            "kind": str(proposal.kind),
            "name": proposal.name,
            "path": proposal.target,
            "status": str(proposal.status),
            "fingerprint": proposal.fingerprint[:16],
            "reloaded": True,
        }

    def dev_list(self, status: str | None = None, limit: int = 20) -> list[ChangeProposal]:
        return self.workbench.list(status=status, limit=limit)

    def dev_show(self, proposal_id: str) -> ChangeProposal:
        return self.workbench.get(proposal_id)

    def propose_change(
        self,
        kind: str,
        name: str,
        content: str,
        *,
        origin: str = "human:cli",
        rationale: str = "",
    ):
        return self.workbench.propose(kind, name, content, origin=origin, rationale=rationale)

    def security_status(self) -> dict[str, Any]:
        """Raio-X da postura de segurança — o que está verificado e o que não está."""

        principals = self.identity.list()
        key = self.keystore.load()
        return {
            "identity_required": self.settings.config.security.identity_required,
            "allow_agent_approval": self.settings.config.security.allow_agent_approval,
            "approval_min_role": self.settings.config.security.approval_min_role,
            "identities": {
                "total": len(principals),
                "active": len([item for item in principals if item.active]),
                "humans": len([item for item in principals if item.kind == PrincipalKind.HUMAN]),
                "agents": len([item for item in principals if item.kind == PrincipalKind.AGENT]),
                "services": len([item for item in principals if item.kind == PrincipalKind.SERVICE]),
                "by_role": {
                    role: len([item for item in principals if role in item.roles]) for role in ROLES
                },
                "tokens_active": len([token for token in self.identity.tokens() if token.usable]),
            },
            "vault": self.vault.status(),
            "master_key_history": self.key_repository.list(),
            "rbac": {"roles": list(ROLES), "permissions": len(PERMISSIONS)},
            "gaps": self._security_gaps(key),
        }

    def _security_gaps(self, key: MasterKey | None) -> list[str]:
        """Lacunas honestas: o Runtime mostra o que ainda não está garantido."""

        gaps: list[str] = []
        if not self.settings.config.security.identity_required:
            gaps.append("identidade não exigida: aprovações podem ser decididas sem principal verificado")
        if self.identity.count() == 0:
            gaps.append("nenhum principal cadastrado: rode `egr identity add <id> --roles approver`")
        if key is None:
            gaps.append("cofre sem chave mestra: rode `egr key init`")
        problem = self.keystore.permission_problem()
        if problem:
            gaps.append(problem)
        if not self.settings.config.security.allow_agent_approval:
            gaps.append("agentes não podem aprovar (padrão seguro)")
        return gaps

    def init_master_key(self, *, actor: str = "cli", force: bool = False) -> dict:
        key = self.keystore.create(force=force)
        self.key_repository.record(key.key_id, source=key.source, actor=actor, rotated=False)
        self.audit.record(
            EventType.KEY_INITIALIZED,
            actor=actor,
            payload={"key_id": key.key_id, "source": key.source},
        )
        return self.keystore.status()

    def rotate_master_key(self, *, actor: str = "cli", force: bool = False) -> dict:
        """Gera nova chave mestra e recifra todo o cofre. Nada fica em claro."""

        previous, current = self.keystore.rotate(force=force)
        reencrypted = self.vault.reencrypt_all(previous, current)
        self.key_repository.record(current.key_id, source=current.source, actor=actor, rotated=True)
        self.audit.record(
            EventType.KEY_ROTATED,
            actor=actor,
            payload={
                "key_id": current.key_id,
                "previous_key_id": previous.key_id if previous else None,
                "secrets_reencrypted": reencrypted,
            },
        )
        return {
            "key_id": current.key_id,
            "previous_key_id": previous.key_id if previous else None,
            "secrets_reencrypted": reencrypted,
            "status": self.keystore.status(),
        }

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
            "sandbox": self.sandbox_info,
            "memory": self.memory.stats(),
            "orchestration": self.orchestration_status(),
            "development": self.dev_status(),
            "evaluation": self.evaluation_status(),
            "governance": self.governance_status(),
            "channels": self.channel_status(),
            "integrations": self.integrations_status(),
            "packs": self.packs_status(),
            "security": self.security_status(),
            "mcp": {
                "enabled": self.settings.config.mcp.enabled,
                "servers": len(self.settings.config.mcp.servers),
                "tools": [tool["name"] for tool in self.tools.list() if tool["name"].startswith("mcp.")],
                "failures": self.mcp_failures,
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
        sandbox = self.sandbox_info
        add(
            "sandbox",
            self.settings.sandbox_path.exists(),
            f"{self.settings.sandbox_path} (mode={sandbox['mode']}, "
            f"python_exec={self.settings.config.security.python_exec_enabled})",
        )
        if sandbox["mode"] != "container":
            checks.append(
                {
                    "check": "sandbox:isolamento",
                    "ok": False,
                    "detail": (
                        "sandbox em modo 'process': sem contêiner, o isolamento depende de "
                        "política e confinamento de paths (instale Docker/Podman para modo container)"
                    ),
                }
            )
        for failure in self.mcp_failures:
            checks.append(
                {
                    "check": f"mcp:{failure['server']}",
                    "ok": False,
                    "detail": failure["error"][:120],
                }
            )
        security = self.security_status()
        add(
            "security:chave",
            security["vault"]["master_key"]["present"],
            f"chave mestra: {security['vault']['master_key']['key_id'] or 'ausente'} "
            f"({security['vault']['master_key']['source'] or '-'})",
        )
        if not self.settings.config.security.identity_required:
            checks.append(
                {
                    "check": "security:identidade",
                    "ok": False,
                    "detail": (
                        "aprovações podem ser decididas sem principal verificado "
                        "(ative security.identity_required e cadastre identidades: `egr identity add`)"
                    ),
                }
            )
        problem = self.keystore.permission_problem()
        if problem:
            checks.append({"check": "security:chave_permissoes", "ok": False, "detail": problem})
        orchestration = self.orchestration_status()
        add(
            "workflows",
            bool(self.workflows),
            f"{orchestration['workflows']} workflow(s), "
            f"{orchestration['runs']['total']} execuções, "
            f"{len(orchestration['triggers'])} gatilho(s)",
        )
        for item in self.scheduler.due():
            if "error" in item:
                checks.append(
                    {"check": f"cron:{item['workflow']}", "ok": False, "detail": item["error"]}
                )
        memory_stats = self.memory.stats()
        add(
            "memory",
            memory_stats["total"] > 0,
            f"{memory_stats['total']} registros "
            f"({memory_stats['active']} ativos, {memory_stats['archived']} arquivados)",
        )
        if memory_stats["without_vector"]:
            checks.append(
                {
                    "check": "memory:vetores",
                    "ok": False,
                    "detail": (
                        f"{memory_stats['without_vector']} registro(s) sem vetor semântico "
                        "(`egr memory reindex` reconstrói)"
                    ),
                }
            )
        for name, report in self.gateway.health().items():
            add(f"model:{name}", report["healthy"], report["detail"])
        governance = self.governance_status()
        add(
            "governance",
            governance["releases"]["total"] > 0 or governance["versions"]["total"] == 0,
            f"{governance['releases']['total']} release(s), "
            f"{governance['versions']['total']} versão(ões) de artefato, "
            f"aplicados: {', '.join(f'{k}={len(v)}' for k, v in governance['deployed'].items()) or '-'}",
        )
        channels = self.channel_status()
        add(
            "channels",
            not channels["habilitado"] or bool(channels["canais"]),
            f"{len(channels['canais'])} canal(is), "
            f"{channels['pareamentos']['total']} pareamento(s), "
            f"{channels['mensagens']['total']} mensagem(ns)"
            + ("" if channels["habilitado"] else " (gateway desabilitado)"),
        )
        if channels["habilitado"] and not channels["pareamento_exigido"]:
            checks.append(
                {
                    "check": "channels:pareamento",
                    "ok": False,
                    "detail": (
                        "gateway sem pareamento obrigatório: qualquer remetente que descobrir o "
                        "canal fala com o Runtime (mantenha gateway.require_pairing: true)"
                    ),
                }
            )

        packs = self.packs_status()
        add(
            "packs",
            True,
            f"{packs['catálogo']['total']} pack(s) no catálogo, {packs['instalados']} instalado(s)",
        )
        for item in packs["pacotes"]:
            if item["status"] == "outdated":
                checks.append(
                    {
                        "check": f"pack:{item['id']}",
                        "ok": False,
                        "detail": (
                            f"pack '{item['id']}' instalado em versão diferente da do catálogo "
                            f"(reinstale para atualizar)"
                        ),
                    }
                )

        integrations = self.integrations_status()
        enabled_connectors = [
            item for item in integrations["conectores"]["itens"] if item["habilitado"]
        ]
        add(
            "integrations",
            not integrations["habilitado"] or bool(integrations["conectores"]["total"]),
            f"{integrations['conectores']['total']} conector(es) "
            f"({len(enabled_connectors)} habilitado(s)), "
            f"{integrations['chamadas']['total']} chamada(s), "
            f"{integrations['eventos']['total']} evento(s)"
            + ("" if integrations["habilitado"] else " (integrações desabilitadas)"),
        )
        fila = integrations["fila"]
        falhas = int((fila.get("por_status") or {}).get("failed", 0))
        if falhas:
            checks.append(
                {
                    "check": "integracoes:fila",
                    "ok": False,
                    "detail": (
                        f"{falhas} job(s) de integração esgotaram as tentativas: "
                        "veja `egr integration jobs --status failed`"
                    ),
                }
            )
        for item in enabled_connectors:
            if item["tipo"] not in ("rest", "graphql"):
                continue  # SQL não sai por HTTP: a fronteira é o driver declarado
            if item["hosts"] in ("-", ""):
                checks.append(
                    {
                        "check": f"integracao:{item['id']}",
                        "ok": False,
                        "detail": (
                            f"conector '{item['id']}' habilitado sem lista branca de hosts: "
                            "declare allowed_hosts (ou desabilite o conector)"
                        ),
                    }
                )

        evaluation = self.evaluation_status()
        add(
            "evaluation",
            evaluation["suites"]["total"] > 0,
            f"{evaluation['suites']['total']} suíte(s), "
            f"{evaluation['runs']['total']} execução(ões), "
            f"vereditos: {', '.join(f'{k}={v}' for k, v in sorted(evaluation['runs']['by_status'].items())) or '-'}",
        )
        for suite_id, summary in evaluation["runs"]["last_by_suite"].items():
            if summary["status"] in ("failed", "regressed", "error"):
                checks.append(
                    {
                        "check": f"avaliacao:{suite_id}",
                        "ok": False,
                        "detail": (
                            f"{summary['status']}: "
                            f"{'; '.join(summary['reasons'][:2]) or 'sem motivo registrado'}"
                        ),
                    }
                )

        dev = self.dev_status()
        add(
            "development",
            True,
            f"{dev['proposals']['total']} proposta(s), "
            f"{dev['proposals']['awaiting_approval']} aguardando aprovação, "
            f"{len(dev['workspace_tools']['files'])} ferramenta(s) do workspace",
        )
        for rejection in dev["workspace_tools"].get("rejected") or []:
            checks.append(
                {
                    "check": f"dev:ferramenta:{rejection['file']}",
                    "ok": False,
                    "detail": "; ".join(rejection["problems"][:2]),
                }
            )
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
