"""Local API + console do Runtime.

A interface é descartável; o núcleo é o Runtime. Telegram/Slack/Web são
gateways sobre esta mesma API (Fase 10).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..core.errors import AuthenticationError, AuthorizationError, ConfigError
from ..domain.evaluation import EvaluationSuite
from ..evaluation.security import scan as security_scan
from ..runtime.runtime import Runtime
from ..security.rbac import TASK_SUBMIT
from ..security.rbac import require as require_permission
from ..version import PHASE, __version__

TAGS = [
    {"name": "runtime", "description": "Estado e saúde do Runtime"},
    {"name": "tasks", "description": "Submissão e inspeção de trabalho"},
    {"name": "governance", "description": "Políticas, aprovações e auditoria"},
    {"name": "security", "description": "Identidade, RBAC, cofre e chaves"},
    {"name": "orchestration", "description": "Workflows, execuções, agenda e webhooks"},
    {"name": "development", "description": "Propostas de mudança: criar agents/tools/workflows sob governo"},
    {"name": "evaluation", "description": "Casos, métricas, baseline, regressão e varredura de segurança"},
    {"name": "release", "description": "Promoção dev → staging → produção, versões e rollback"},
    {"name": "gateway", "description": "Canais (Telegram/Slack/Web): mensagem entra, task governada sai"},
    {
        "name": "integrations",
        "description": "Conectores (REST/GraphQL/SQL/webhook): chamada governada e evento idempotente",
    },
    {"name": "packs", "description": "Packs verticais: catálogo instalado por proposta aprovada"},
]


class TaskCreate(BaseModel):
    objective: str
    agent_id: str | None = None
    environment: str | None = None


class WorkflowRunRequest(BaseModel):
    inputs: dict = {}
    environment: str | None = None
    created_by: str = "api"


class MemoryWrite(BaseModel):
    content: str
    kind: str = "knowledge"
    namespace: str = "default"
    tags: list[str] = []
    importance: float | None = None


class ProposalCreate(BaseModel):
    kind: str
    name: str
    content: str
    rationale: str = ""
    origin: str = "human:api"


class ProposalDecision(BaseModel):
    by: str = "human:api"
    note: str = ""
    args: dict = {}
    timeout: int = 10


class IntegrationJobRequest(BaseModel):
    method: str = "GET"
    path: str = ""
    query: str = ""
    body: str | None = None
    variables: dict[str, Any] = {}
    idempotency: str | None = None
    max_attempts: int | None = None
    by: str = "human:api"


class IntegrationCallRequest(BaseModel):
    method: str = "GET"
    path: str = ""
    query: str = ""
    body: str | None = None
    variables: dict[str, Any] = {}
    dry_run: bool = False
    by: str = "human:api"


class GatewayMessageRequest(BaseModel):
    channel: str = "web"
    external_id: str = "anonimo"
    text: str
    display_name: str = ""
    #: lacuna 10b: arquivos junto com a mensagem (nome, tipo e conteúdo em base64)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class GatewayAttachmentRequest(BaseModel):
    """Lacuna 10b: upload direto (o canal web não tem `file_id` para oferecer)."""

    channel: str = "web"
    external_id: str = "anonimo"
    name: str
    mime: str = ""
    content_base64: str = ""
    text: str = ""
    display_name: str = ""


class GatewayInteractionRequest(BaseModel):
    """Lacuna 10b: um botão apertado — vira comando, nunca execução direta."""

    channel: str
    external_id: str
    action: str
    value: str = ""
    display_name: str = ""


class GatewayPairRequest(BaseModel):
    channel: str
    external_id: str
    code: str | None = None
    roles: list[str] | None = None
    display_name: str = ""
    by: str = "human:api"


class ReleaseItemRequest(BaseModel):
    kind: str
    name: str


class ReleaseCreateRequest(BaseModel):
    items: list[ReleaseItemRequest]
    target: str = "staging"
    title: str = ""
    reason: str = ""
    created_by: str = "api"


class ReleaseDecisionRequest(BaseModel):
    by: str = "human:api"
    token: str | None = None
    note: str = ""


class EvaluationRunRequest(BaseModel):
    baseline: str | None = None
    actor: str = "api"


class DecisionRequest(BaseModel):
    decision: str = "approve"  # approve | deny
    by: str = "console"
    note: str | None = None


def bearer_token(authorization: str | None) -> str | None:
    """Extrai o token de `Authorization: Bearer egr_<id>.<segredo>`."""

    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip() or None


def _decode(content_base64: str) -> bytes:
    """Base64 do upload: erro de codificação é 400, não exceção interna."""

    import base64
    import binascii

    try:
        return base64.b64decode(content_base64 or "", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"conteúdo base64 inválido: {exc}") from exc


def _decode_attachments(items: list[dict[str, Any]]):
    from ..domain.channel import InboundAttachment

    decoded = []
    for item in items or []:
        content = _decode(item.get("content_base64", "")) if item.get("content_base64") else None
        decoded.append(
            InboundAttachment(
                name=str(item.get("name") or "anexo"),
                mime=str(item.get("mime") or ""),
                size=len(content) if content is not None else int(item.get("size") or 0),
                content=content,
                remote_ref=str(item.get("remote_ref") or "upload"),
            )
        )
    return decoded


def create_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(
        title="Enterprise AGI Runtime",
        version=__version__,
        description=f"{PHASE}. API local do Runtime.",
        openapi_tags=TAGS,
    )

    # ------------------------------------------------------------------
    @app.get("/health", tags=["runtime"])
    def health() -> dict[str, Any]:
        return runtime.health()

    @app.get("/v1/status", tags=["runtime"])
    def status() -> dict[str, Any]:
        return runtime.status()

    @app.get("/v1/agents", tags=["runtime"])
    def agents() -> list[dict[str, Any]]:
        return [agent.model_dump(mode="json") for agent in runtime.agents.values()]

    @app.get("/v1/tools", tags=["runtime"])
    def tools() -> list[dict[str, Any]]:
        return runtime.tools.list()

    @app.get("/v1/models", tags=["runtime"])
    def models() -> dict[str, Any]:
        return runtime.gateway.health()

    @app.get("/v1/usage", tags=["runtime"])
    def usage(task_id: str | None = None, since: str | None = None) -> dict[str, Any]:
        """Custo, latência e tokens agregados (Fase 2)."""

        return runtime.usage.totals(task_id=task_id, since=since)

    # ------------------------------------------------------------------
    @app.get("/v1/tasks", tags=["tasks"])
    def list_tasks(limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
        return [
            task.model_dump(mode="json")
            for task in runtime.tasks.list(status=status, limit=limit)
        ]

    @app.post("/v1/tasks", tags=["tasks"])
    def create_task(payload: TaskCreate) -> dict[str, Any]:
        task = runtime.submit(
            payload.objective,
            agent_id=payload.agent_id,
            environment=payload.environment,
            created_by="api",
        )
        return task.model_dump(mode="json")

    @app.get("/v1/tasks/{task_id}", tags=["tasks"])
    def get_task(task_id: str) -> dict[str, Any]:
        task = runtime.tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task.model_dump(mode="json")

    @app.get("/v1/tasks/{task_id}/events", tags=["tasks"])
    def task_events(task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [event.model_dump(mode="json") for event in runtime.audit.list(task_id=task_id, limit=limit)]

    # ------------------------------------------------------------------
    @app.get("/v1/approvals", tags=["governance"])
    def approvals(status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [approval.model_dump(mode="json") for approval in runtime.approvals.list(status=status, limit=limit)]

    @app.post("/v1/approvals/{approval_id}/decision", tags=["governance"])
    def decide(
        approval_id: str,
        payload: DecisionRequest,
        authorization: str | None = Header(None, description="Bearer egr_<id>.<segredo>"),
    ) -> dict[str, Any]:
        """Decisão humana. Com `identity_required`, exige credencial verificável."""

        token = bearer_token(authorization)
        try:
            if payload.decision == "deny":
                task = runtime.deny(approval_id, decided_by=payload.by, note=payload.note, token=token)
            else:
                task = runtime.approve(approval_id, decided_by=payload.by, note=payload.note, token=token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {
            "approval": approval_id,
            "decision": payload.decision,
            "task": task.model_dump(mode="json") if task else None,
        }

    # ------------------------------------------------------------------
    @app.get("/v1/workflows", tags=["orchestration"])
    def workflows() -> list[dict[str, Any]]:
        runtime._load_workflows()
        return [
            {
                "id": workflow.id,
                "name": workflow.name,
                "version": workflow.version,
                "environment": str(workflow.environment),
                "trigger": workflow.trigger.type,
                "event": workflow.trigger.event,
                "cron": workflow.trigger.cron,
                "steps": len(workflow.steps),
                "parallel": workflow.parallel,
                "problems": runtime.orchestrator.validate(workflow),
            }
            for workflow in runtime.workflows.values()
        ]

    @app.post("/v1/workflows/{workflow_id}/run", tags=["orchestration"])
    def run_workflow(workflow_id: str, payload: WorkflowRunRequest | None = None) -> dict[str, Any]:
        payload = payload or WorkflowRunRequest()
        try:
            run = runtime.run_workflow(
                workflow_id,
                inputs=payload.inputs,
                environment=payload.environment,
                created_by=payload.created_by,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return run.model_dump(mode="json")

    @app.get("/v1/workflow-runs", tags=["orchestration"])
    def workflow_runs(
        workflow_id: str | None = None, status: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        return [run.model_dump(mode="json") for run in runtime.runs.list(
            workflow_id=workflow_id, status=status, limit=limit
        )]

    @app.get("/v1/workflow-runs/{run_id}", tags=["orchestration"])
    def workflow_run(run_id: str) -> dict[str, Any]:
        run = runtime.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run.model_dump(mode="json")

    @app.post("/v1/workflow-runs/{run_id}/resume", tags=["orchestration"])
    def resume_workflow(run_id: str) -> dict[str, Any]:
        try:
            return runtime.orchestrator.resume(run_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/webhooks/{workflow_id}", tags=["orchestration"])
    def webhook(
        workflow_id: str,
        payload: WorkflowRunRequest | None = None,
        authorization: str | None = Header(None, description="Bearer egr_<id>.<segredo>"),
    ) -> dict[str, Any]:
        """Dispara um workflow por webhook.

        Com `security.identity_required`, exige um principal com `task.submit`
        (mesma regra de qualquer ação no Runtime — webhook não é exceção).
        """

        payload = payload or WorkflowRunRequest()
        if runtime.settings.config.security.identity_required:
            principal = runtime.identity.resolve(bearer_token(authorization))
            if principal is None:
                raise HTTPException(status_code=401, detail="webhook exige credencial verificável")
            try:
                require_permission(principal, TASK_SUBMIT)
            except AuthorizationError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            payload.created_by = principal.id
        try:
            run = runtime.run_workflow(
                workflow_id,
                inputs=payload.inputs,
                environment=payload.environment,
                created_by=payload.created_by,
                trigger="webhook",
                trigger_detail=f"webhook:{workflow_id}",
            )
        except ConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return run.model_dump(mode="json")

    @app.get("/v1/schedule", tags=["orchestration"])
    def schedule(limit: int = 5) -> dict[str, Any]:
        """Agenda: o que está vencido agora e os próximos disparos."""

        return {"due": runtime.scheduler.due(), "upcoming": runtime.scheduler.upcoming(limit=limit)}

    # ------------------------------------------------------------------
    @app.get("/v1/dev", tags=["development"])
    def dev_status() -> dict[str, Any]:
        """Ambiente de desenvolvimento: propostas, ferramentas do workspace."""

        return runtime.dev_status()

    @app.get("/v1/dev/proposals", tags=["development"])
    def dev_proposals(
        status: str | None = None, kind: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        return [proposal.summary() for proposal in runtime.workbench.list(status=status, kind=kind, limit=limit)]

    @app.post("/v1/dev/proposals", tags=["development"])
    def dev_propose(payload: ProposalCreate) -> dict[str, Any]:
        """Cria uma proposta. Nunca aplica: isso é ato humano."""

        try:
            proposal = runtime.workbench.propose(
                payload.kind,
                payload.name,
                payload.content,
                origin=payload.origin,
                rationale=payload.rationale,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return proposal.model_dump(mode="json")

    @app.get("/v1/dev/proposals/{proposal_id}", tags=["development"])
    def dev_proposal(proposal_id: str) -> dict[str, Any]:
        proposal = runtime.proposals.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        return proposal.model_dump(mode="json")

    @app.post("/v1/dev/proposals/{proposal_id}/validate", tags=["development"])
    def dev_validate(proposal_id: str) -> dict[str, Any]:
        try:
            return runtime.workbench.validate(proposal_id).model_dump(mode="json")
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/dev/proposals/{proposal_id}/test", tags=["development"])
    def dev_test(proposal_id: str, payload: ProposalDecision | None = None) -> dict[str, Any]:
        payload = payload or ProposalDecision()
        try:
            proposal = runtime.workbench.trial(proposal_id, payload.args, timeout=payload.timeout)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return proposal.model_dump(mode="json")

    @app.post("/v1/dev/proposals/{proposal_id}/approve", tags=["development"])
    def dev_approve(proposal_id: str, payload: ProposalDecision | None = None) -> dict[str, Any]:
        payload = payload or ProposalDecision()
        try:
            return runtime.workbench.approve(proposal_id, actor=payload.by, note=payload.note).model_dump(mode="json")
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/dev/proposals/{proposal_id}/reject", tags=["development"])
    def dev_reject(proposal_id: str, payload: ProposalDecision | None = None) -> dict[str, Any]:
        payload = payload or ProposalDecision()
        try:
            return runtime.workbench.reject(proposal_id, actor=payload.by, note=payload.note).model_dump(mode="json")
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/dev/proposals/{proposal_id}/apply", tags=["development"])
    def dev_apply(proposal_id: str, payload: ProposalDecision | None = None) -> dict[str, Any]:
        """Aplica uma proposta aprovada. Exige identidade quando ativada."""

        payload = payload or ProposalDecision()
        try:
            return runtime.workbench.apply(proposal_id, actor=payload.by).model_dump(mode="json")
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    @app.get("/v1/eval", tags=["evaluation"])
    def evaluation() -> dict[str, Any]:
        """Estado da avaliação: suítes, vereditos, custo e latência."""

        return runtime.evaluation_status()

    @app.get("/v1/eval/suites", tags=["evaluation"])
    def eval_suites() -> list[dict[str, Any]]:
        return [suite.summary() for suite in runtime.evaluation_suites.values()]

    @app.post("/v1/eval/suites", tags=["evaluation"])
    def eval_add_suite(payload: EvaluationSuite) -> dict[str, Any]:
        """Registra uma suíte (o YAML versionado continua sendo a fonte)."""

        runtime.suites.save(payload)
        runtime.evaluation_suites[payload.id] = payload
        return payload.model_dump(mode="json")

    @app.post("/v1/eval/suites/{suite_id}/run", tags=["evaluation"])
    def eval_run_suite(suite_id: str, payload: EvaluationRunRequest | None = None) -> dict[str, Any]:
        payload = payload or EvaluationRunRequest()
        suite = runtime.evaluation_suites.get(suite_id) or runtime.suites.get(suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="suite not found")
        try:
            run = runtime.evaluator.run(suite, baseline=payload.baseline, actor=payload.actor)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return run.model_dump(mode="json")

    @app.get("/v1/eval/runs", tags=["evaluation"])
    def eval_runs(suite_id: str | None = None, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [
            run.summary()
            for run in runtime.evaluations.list(suite_id=suite_id, status=status, limit=limit)
        ]

    @app.get("/v1/eval/runs/{run_id}", tags=["evaluation"])
    def eval_run(run_id: str) -> dict[str, Any]:
        run = runtime.evaluations.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run.model_dump(mode="json")

    @app.post("/v1/eval/runs/{run_id}/baseline", tags=["evaluation"])
    def eval_baseline(run_id: str) -> dict[str, Any]:
        """Promove uma execução a referência da suíte."""

        run = runtime.evaluations.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        suite = runtime.evaluation_suites.get(run.suite_id) or runtime.suites.get(run.suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="suite not found")
        suite.metadata["baseline_run"] = run.id
        runtime.suites.save(suite)
        runtime.evaluation_suites[suite.id] = suite
        return {"suite": suite.id, "baseline": run.id, "veredito": str(run.status)}

    @app.get("/v1/eval/security/{target_kind}/{target}", tags=["evaluation"])
    def eval_security(target_kind: str, target: str) -> list[dict[str, Any]]:
        """Varredura de segurança de um artefato (sem executá-lo)."""

        return [finding.model_dump(mode="json") for finding in security_scan(runtime, target_kind, target)]

    @app.get("/v1/release", tags=["release"])
    def release_status() -> dict[str, Any]:
        """Onde cada artefato está na escada de ambientes."""

        return runtime.governance_status()

    @app.post("/v1/release", tags=["release"])
    def release_create(request: ReleaseCreateRequest) -> dict[str, Any]:
        """Cria um release: tira snapshot dos itens e confere os gates."""

        try:
            release = runtime.release_manager.create(
                [(item.kind, item.name) for item in request.items],
                target=request.target,
                title=request.title or "",
                reason=request.reason or "",
                created_by=request.created_by or "api",
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return release.summary()

    @app.get("/v1/releases", tags=["release"])
    def release_list(status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Histórico de promoções (o que foi promovido, por quem e com qual evidência)."""

        return [release.summary() for release in runtime.release_manager.list(status=status, limit=limit)]

    @app.get("/v1/release/{release_id}", tags=["release"])
    def release_show(release_id: str) -> dict[str, Any]:
        """Um release por completo: itens, gates, evidência e decisão."""

        try:
            return runtime.release_manager.get(release_id).model_dump(mode="json")
        except ConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/release/{release_id}/check", tags=["release"])
    def release_check(release_id: str) -> dict[str, Any]:
        """Reconfere os gates (a evidência pode ter mudado desde a criação)."""

        try:
            return runtime.release_manager.check(release_id).summary()
        except ConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/release/{release_id}/submit", tags=["release"])
    def release_submit(release_id: str) -> dict[str, Any]:
        """Submete à aprovação humana. Falha se os gates reprovarem."""

        try:
            return runtime.release_manager.submit(release_id).summary()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/release/{release_id}/approve", tags=["release"])
    def release_approve(release_id: str, request: ReleaseDecisionRequest) -> dict[str, Any]:
        """Aprovação humana. Produção exige papel mais alto que staging."""

        try:
            release = runtime.release_manager.approve(
                release_id, request.by, token=request.token, note=request.note or ""
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return release.summary()

    @app.post("/v1/release/{release_id}/reject", tags=["release"])
    def release_reject(release_id: str, request: ReleaseDecisionRequest) -> dict[str, Any]:
        """Recusa um release."""

        try:
            return runtime.release_manager.reject(release_id, request.by, note=request.note or "").summary()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/release/{release_id}/deploy", tags=["release"])
    def release_deploy(release_id: str, request: ReleaseDecisionRequest) -> dict[str, Any]:
        """Aplica um release aprovado no ambiente de destino."""

        try:
            return runtime.release_manager.deploy(release_id, actor=request.by).summary()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/release/{release_id}/rollback", tags=["release"])
    def release_rollback(release_id: str, request: ReleaseDecisionRequest) -> dict[str, Any]:
        """Volta cada item para a revisão anterior à deste release."""

        try:
            return runtime.release_manager.rollback(release_id, actor=request.by, note=request.note or "").summary()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/release/versions/{kind}/{name}", tags=["release"])
    def release_versions(kind: str, name: str) -> list[dict[str, Any]]:
        """Versões (snapshots) de um artefato — o que o rollback pode restaurar."""

        return [
            version.model_dump(mode="json")
            for version in runtime.release_manager.versions.versions(kind, name)
        ]

    # ------------------------------------------------------------------
    @app.get("/v1/gateway", tags=["gateway"])
    def gateway_status() -> dict[str, Any]:
        """Canais, pareamentos e mensagens — por onde o Runtime conversa."""

        return runtime.channel_status()

    @app.get("/v1/gateway/channels", tags=["gateway"])
    def gateway_channels() -> list[dict[str, Any]]:
        return runtime.channel_status()["canais"]

    @app.get("/v1/gateway/bindings", tags=["gateway"])
    def gateway_bindings(status: str | None = None, channel: str | None = None) -> list[dict[str, Any]]:
        """Quem está autorizado a falar com o Runtime, e com quais papéis."""

        return [
            binding.summary()
            for binding in runtime.channels.list_bindings(status=status, channel=channel, limit=100)
        ]

    @app.post("/v1/gateway/pair", tags=["gateway"])
    def gateway_pair(request: GatewayPairRequest) -> dict[str, Any]:
        """Aprova o pareamento de um remetente (ato de operador)."""

        try:
            binding = runtime.channels.pair(
                request.channel,
                request.external_id,
                roles=request.roles,
                code=request.code,
                actor=request.by,
                display_name=request.display_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return binding.summary()

    @app.delete("/v1/gateway/bindings/{channel}/{external_id}", tags=["gateway"])
    def gateway_unpair(channel: str, external_id: str, block: bool = False, by: str = "human:api") -> dict[str, Any]:
        try:
            binding = runtime.channels.unpair(channel, external_id, actor=by, block=block)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return binding.summary()

    @app.post("/v1/gateway/messages", tags=["gateway"])
    def gateway_message(request: GatewayMessageRequest) -> dict[str, Any]:
        """Entrega uma mensagem ao Gateway (o canal web usa esta rota)."""

        from ..domain.channel import InboundMessage

        message = InboundMessage(
            channel=request.channel,
            external_id=request.external_id,
            text=request.text,
            display_name=request.display_name,
            attachments=_decode_attachments(request.attachments),
        )
        return runtime.channels.handle_inbound(message).summary()

    @app.get("/v1/gateway/attachments", tags=["gateway"])
    def gateway_attachments(
        channel: str | None = None,
        external_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ):
        """Anexos que entraram — aceitos e recusados, com motivo."""

        return [
            item.summary()
            for item in runtime.gateway_attachments.list(
                channel=channel, external_id=external_id, status=status, limit=limit
            )
        ]

    @app.get("/v1/gateway/attachments/{attachment_id}", tags=["gateway"])
    def gateway_attachment(attachment_id: str) -> dict[str, Any]:
        """Um anexo: caminho, impressão digital, situação e trecho redigido."""

        item = runtime.gateway_attachments.get(attachment_id)
        if item is None:
            raise HTTPException(status_code=404, detail="anexo não encontrado")
        return {**item.summary(), "trecho": item.preview}

    @app.post("/v1/gateway/attachments", tags=["gateway"])
    def gateway_attachment_upload(request: GatewayAttachmentRequest) -> dict[str, Any]:
        """Recebe um arquivo (com ou sem mensagem) pela fronteira governada."""

        from ..domain.channel import InboundAttachment, InboundMessage

        binding = runtime.gateway_bindings.get(f"{request.channel}:{request.external_id}")
        if runtime.settings.config.gateway.require_pairing and (binding is None or not binding.active):
            raise HTTPException(status_code=403, detail="remetente sem pareamento ativo")
        content = _decode(request.content_base64)
        pending = InboundAttachment(
            name=request.name, mime=request.mime, size=len(content), content=content, remote_ref="upload"
        )
        actor = binding.principal_id if binding else f"{request.channel}:{request.external_id}"
        stored = runtime.channels.attachments.receive_many(
            request.channel, request.external_id, [pending], actor=actor
        )
        if request.text.strip():
            reply = runtime.channels.handle_inbound(
                InboundMessage(
                    channel=request.channel,
                    external_id=request.external_id,
                    text=request.text,
                    display_name=request.display_name,
                )
            )
            for item in stored:
                if item.stored:
                    item.attach(reply.task_id or "")
                    runtime.gateway_attachments.save(item)
            return {**reply.summary(), "anexo": stored[0].summary()}
        return {"anexo": stored[0].summary()}

    @app.post("/v1/gateway/interactions", tags=["gateway"])
    def gateway_interaction(request: GatewayInteractionRequest) -> dict[str, Any]:
        """Botão apertado: vira o comando que ele representa — governado igual."""

        return runtime.channels.handle_interaction(
            request.channel,
            request.external_id,
            request.action,
            request.value,
            display_name=request.display_name,
        ).summary()

    @app.get("/v1/gateway/messages", tags=["gateway"])
    def gateway_messages(channel: str | None = None, direction: str | None = None, limit: int = 20):
        """Histórico redigido da conversa."""

        return [
            message.summary()
            for message in runtime.gateway_messages.list(channel=channel, direction=direction, limit=limit)
        ]

    @app.post("/v1/gateway/slack/events", tags=["gateway"])
    async def gateway_slack(request: Request) -> Any:
        """Events API do Slack: assinatura conferida antes de qualquer leitura."""

        from ..gateway.slack import SlackChannel, verify_signature

        channel = next(
            (
                item
                for item in runtime.channels.select()
                if isinstance(item, SlackChannel)
            ),
            None,
        )
        if channel is None:
            raise HTTPException(status_code=404, detail="canal slack não configurado")
        body = (await request.body()).decode("utf-8")
        timestamp = request.headers.get("x-slack-request-timestamp", "")
        signature = request.headers.get("x-slack-signature", "")
        if not verify_signature(channel.signing_secret, timestamp, body, signature):
            runtime.audit.record(
                "gateway.denied",
                actor="slack",
                environment=str(runtime.settings.environment),
                payload={"motivo": "assinatura inválida"},
            )
            raise HTTPException(status_code=401, detail="assinatura inválida")
        payload = json.loads(body or "{}")
        reply = channel.handle_payload(payload, runtime.channels.handle_inbound)
        if reply is None:
            return {"ok": True, "handled": False}
        if reply.command == "url_verification":
            return {"challenge": reply.text}
        return {"ok": True, "handled": True, "reply": reply.summary()}

    @app.post("/v1/gateway/telegram/webhook", tags=["gateway"])
    async def gateway_telegram(request: Request) -> Any:
        """Webhook do Telegram (alternativa ao long polling)."""

        from ..gateway.telegram import TelegramChannel

        channel = next(
            (item for item in runtime.channels.select() if isinstance(item, TelegramChannel)),
            None,
        )
        if channel is None:
            raise HTTPException(status_code=404, detail="canal telegram não configurado")
        secret = channel.webhook_secret
        if secret and request.headers.get("x-telegram-bot-api-secret-token") != secret:
            raise HTTPException(status_code=401, detail="token do webhook inválido")
        payload = json.loads((await request.body()).decode("utf-8") or "{}")
        reply = channel.handle_update(payload, runtime.channels.handle_inbound)
        return {"ok": True, "handled": reply is not None}

    # ------------------------------------------------------------------
    @app.get("/v1/integrations", tags=["integrations"])
    def integrations() -> dict[str, Any]:
        """Conectores declarados, chamadas recentes e eventos de entrada."""

        return runtime.integrations_status()

    @app.get("/v1/integrations/calls", tags=["integrations"])
    def integration_calls(integration: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """O que saiu daqui: destino, decisão, latência, custo e ator."""

        return [call.summary() for call in runtime.integration_calls.list(integration=integration, limit=limit)]

    @app.get("/v1/integrations/jobs", tags=["integrations"])
    def integration_jobs(status: str | None = None, integration: str | None = None, limit: int = 20):
        """Fila de saída: o que está prometido, o que falhou e por quê."""

        return [
            job.summary()
            for job in runtime.integration_jobs.list(status=status, integration=integration, limit=limit)
        ]

    @app.post("/v1/integrations/{integration_id}/jobs", tags=["integrations"])
    def integration_enqueue(integration_id: str, request: IntegrationJobRequest) -> dict[str, Any]:
        """Enfileira uma chamada (idempotente por chave). O drain executa."""

        try:
            job = runtime.connectors.enqueue(
                integration_id,
                method=request.method,
                path=request.path,
                query=request.query,
                body=request.body,
                variables=request.variables,
                idempotency=request.idempotency,
                max_attempts=request.max_attempts,
                actor=request.by,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return job.summary()

    @app.post("/v1/integrations/jobs/drain", tags=["integrations"])
    def integration_drain(limit: int | None = None) -> list[dict[str, Any]]:
        """Processa os jobs cuja espera venceu."""

        return runtime.connectors.drain(limit)

    @app.delete("/v1/integrations/jobs/{job_id}", tags=["integrations"])
    def integration_cancel(job_id: str) -> dict[str, Any]:
        try:
            return runtime.connectors.cancel(job_id, actor="human:api").summary()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/integrations/events", tags=["integrations"])
    def integration_events(integration: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """O que chegou de fora — e o que o Runtime fez com isso."""

        return [event.summary() for event in runtime.integration_events.list(integration=integration, limit=limit)]

    @app.get("/v1/integrations/{integration_id}", tags=["integrations"])
    def integration_show(integration_id: str) -> dict[str, Any]:
        """O que este conector pode fazer (nunca traz a credencial)."""

        try:
            item = runtime.connectors.get(integration_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return item.model_dump(mode="json")

    @app.post("/v1/integrations/{integration_id}/enable", tags=["integrations"])
    def integration_enable(integration_id: str, disable: bool = False, by: str = "human:api") -> dict[str, Any]:
        """Habilitar/desabilitar é um ato administrativo e fica na trilha."""

        try:
            item = runtime.connectors.get(integration_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        item.enabled = not disable
        runtime.connectors.register(item)
        return item.summary()

    @app.post("/v1/integrations/{integration_id}/call", tags=["integrations"])
    def integration_call(integration_id: str, request: IntegrationCallRequest) -> dict[str, Any]:
        """Chama um conector declarado: política decide, trilha registra."""

        try:
            call = runtime.connectors.call(
                integration_id,
                method=request.method,
                path=request.path,
                query=request.query,
                body=request.body,
                variables=request.variables,
                dry_run=request.dry_run,
                actor=request.by,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return call.summary()

    @app.post("/v1/integrations/{integration_id}/test", tags=["integrations"])
    def integration_test(integration_id: str) -> dict[str, Any]:
        """Teste sem efeito colateral: `GET /`, `select 1` ou introspecção."""

        try:
            return runtime.connectors.test(integration_id, actor="human:api").summary()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/integrations/{integration_id}/events", tags=["integrations"])
    async def integration_receive(integration_id: str, request: Request) -> dict[str, Any]:
        """Webhook de entrada: assinatura conferida, id repetido não reprocessa."""

        body = (await request.body()).decode("utf-8")
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="payload inválido") from exc
        event = runtime.connectors.receive(
            integration_id,
            payload,
            headers=dict(request.headers),
            signature=request.headers.get("x-egr-signature"),
        )
        if str(event.status) == "rejected":
            raise HTTPException(status_code=401, detail=event.error or "evento recusado")
        return event.summary()

    # ------------------------------------------------------------------
    @app.get("/v1/packs", tags=["packs"])
    def packs() -> dict[str, Any]:
        """Catálogo de packs e o que este workspace já instalou."""

        return runtime.packs_status()

    @app.get("/v1/packs/{pack_id}", tags=["packs"])
    def pack_show(pack_id: str) -> dict[str, Any]:
        """O que vem dentro do pack (e o que ele exige)."""

        try:
            item = runtime.packs.get(pack_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {**item.model_dump(mode="json"), "arquivos": runtime.packs.plan(item)}

    @app.get("/v1/packs/{pack_id}/check", tags=["packs"])
    def pack_check(pack_id: str) -> list[dict[str, Any]]:
        """Verificação estática: requisitos, ambiente e colisões."""

        try:
            item = runtime.packs.get(pack_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return runtime.packs.check(item)

    @app.post("/v1/packs/{pack_id}/install", tags=["packs"])
    def pack_install(pack_id: str, by: str = "human:api", rationale: str = "") -> dict[str, Any]:
        """Propõe a instalação. Nada é escrito sem aprovação humana."""

        try:
            proposal = runtime.packs.propose(pack_id, actor=by, rationale=rationale)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return proposal.summary()

    @app.delete("/v1/packs/{pack_id}", tags=["packs"])
    def pack_remove(pack_id: str, by: str = "human:api") -> dict[str, Any]:
        """Remove o que o pack escreveu, preservando o que foi editado depois."""

        try:
            return runtime.packs.remove(pack_id, actor=by)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    @app.get("/v1/security", tags=["security"])
    def security() -> dict[str, Any]:
        """Postura de segurança: identidades, cofre, chave e lacunas."""

        return runtime.security_status()

    @app.get("/v1/security/roles", tags=["security"])
    def security_roles() -> list[dict[str, Any]]:
        from ..security.rbac import describe

        return describe()["roles"]

    @app.get("/v1/principals", tags=["security"])
    def principals() -> list[dict[str, Any]]:
        """Identidades cadastradas (sem tokens e sem segredos)."""

        return [principal.as_row() for principal in runtime.identity.list()]

    @app.get("/v1/principals/whoami", tags=["security"])
    def whoami(authorization: str | None = Header(None)) -> dict[str, Any]:
        """Quem é o portador desta credencial, segundo o Runtime?"""

        return runtime.identity.whoami(bearer_token(authorization))

    @app.get("/v1/events", tags=["governance"])
    def events(limit: int = 50, type: str | None = None) -> list[dict[str, Any]]:
        return [event.model_dump(mode="json") for event in runtime.audit.list(type=type, limit=limit)]

    @app.get("/v1/audit/verify", tags=["governance"])
    def verify() -> dict[str, Any]:
        return runtime.audit.verify()

    @app.get("/v1/memory", tags=["governance"])
    def memory(
        query: str = "",
        limit: int = 10,
        mode: str = "",
        namespace: str = "",
        explain: bool = False,
    ) -> list[dict[str, Any]]:
        """Busca híbrida (léxico + semântico) na memória da empresa."""

        records = (
            runtime.memory.search(
                query,
                limit=limit,
                mode=mode or None,
                namespaces=[namespace] if namespace else None,
                explain=explain,
            )
            if query
            else runtime.memory.list(namespace=namespace or None, limit=limit)
        )
        return [record.model_dump(mode="json") for record in records]

    @app.post("/v1/memory", tags=["governance"])
    def write_memory(payload: MemoryWrite) -> dict[str, Any]:
        from ..domain.enums import MemoryKind

        try:
            kind = MemoryKind(payload.kind)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"kind inválido: {payload.kind}") from exc
        record = runtime.memory.write(
            payload.content,
            kind=kind,
            namespace=payload.namespace,
            tags=payload.tags,
            source="api",
            importance=payload.importance,
        )
        return record.model_dump(mode="json")

    @app.get("/v1/memory/stats", tags=["governance"])
    def memory_stats() -> dict[str, Any]:
        """Tipos, namespaces, vetores e distribuição do ciclo de vida."""

        return runtime.memory.stats()

    # ------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def console() -> str:
        return _console_html()

    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    def chat() -> str:
        """Chat web: a mesma governança do Telegram, sem instalar nada."""

        return _chat_html()

    return app


def _console_html() -> str:
    return """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>EGR Console</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: #0b0f14; color: #d7e0ea; }
  header { padding: 16px 20px; border-bottom: 1px solid #1d2733; display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap; }
  h1 { font-size: 16px; margin: 0; letter-spacing: .08em; text-transform: uppercase; }
  .muted { color: #7c8b9c; }
  main { padding: 20px; display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
  section { background: #111823; border: 1px solid #1d2733; border-radius: 10px; padding: 14px 16px; }
  h2 { font-size: 12px; letter-spacing: .12em; text-transform: uppercase; color: #7c8b9c; margin: 0 0 10px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }
  .card { background: #0e1520; border: 1px solid #1d2733; border-radius: 8px; padding: 10px; }
  .card b { display: block; font-size: 20px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  td, th { text-align: left; padding: 4px 6px; border-bottom: 1px solid #1a2330; vertical-align: top; }
  th { color: #7c8b9c; font-weight: 500; }
  .ok { color: #4ade80; } .bad { color: #f87171; } .warn { color: #fbbf24; }
  button { background: #1d4ed8; color: white; border: 0; border-radius: 6px; padding: 4px 10px; cursor: pointer; font: inherit; }
  button.deny { background: #7f1d1d; margin-left: 6px; }
  code { color: #93c5fd; }
  .row { display: flex; gap: 8px; align-items: center; }
</style>
</head>
<body>
<header>
  <h1>Enterprise AGI Runtime</h1>
  <span class="muted" id="env"></span>
  <span class="muted" id="updated"></span>
  <a class="muted" href="/chat">chat</a>
  <span class="row" style="margin-left:auto">
    <span class="muted">identidade</span>
    <input id="ident" placeholder="token egr_..." size="34"
           style="background:#0e1520;border:1px solid #1d2733;color:#d7e0ea;border-radius:6px;padding:4px 8px;font:inherit" />
    <button onclick="saveIdent()">usar</button>
    <span class="muted" id="whoami"></span>
  </span>
</header>
<main>
  <section style="grid-column: 1 / -1">
    <h2>Status</h2>
    <div class="cards" id="cards"></div>
  </section>
  <section style="grid-column: 1 / -1">
    <h2>Segurança</h2>
    <div class="cards" id="seccards"></div>
    <table id="gaps" style="margin-top:10px"><tbody></tbody></table>
  </section>
  <section>
    <h2>Custo de modelos</h2>
    <div class="cards" id="costcards"></div>
    <table id="usage" style="margin-top:10px"><tbody></tbody></table>
  </section>
  <section>
    <h2>Aprovações pendentes</h2>
    <table id="approvals"><tbody></tbody></table>
  </section>
  <section>
    <h2>Tasks</h2>
    <table id="tasks"><tbody></tbody></table>
  </section>
  <section style="grid-column: 1 / -1">
    <h2>Auditoria (últimos eventos)</h2>
    <table id="events"><tbody></tbody></table>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

const ident = () => localStorage.getItem('egr_token') || '';
const authHeaders = () => ident() ? {'Content-Type':'application/json','Authorization':'Bearer ' + ident()} : {'Content-Type':'application/json'};
function saveIdent() {
  const value = $('ident').value.trim();
  if (value) localStorage.setItem('egr_token', value); else localStorage.removeItem('egr_token');
  load();
}

async function loadIdent() {
  const token = ident();
  $('ident').value = token;
  if (!token) { $('whoami').textContent = 'não autenticado'; return; }
  try {
    const me = await fetch('/v1/principals/whoami', {headers: {Authorization: 'Bearer ' + token}}).then(r => r.json());
    $('whoami').textContent = me.authenticated ? `${me.id} (${(me.roles||[]).join(', ')})` : 'credencial inválida';
  } catch (e) { $('whoami').textContent = 'erro'; }
}

async function load() {
  const [status, approvals, tasks, events, usage, sec] = await Promise.all([
    fetch('/v1/status').then(r => r.json()),
    fetch('/v1/approvals?status=pending').then(r => r.json()),
    fetch('/v1/tasks?limit=10').then(r => r.json()),
    fetch('/v1/events?limit=25').then(r => r.json()),
    fetch('/v1/usage').then(r => r.json()),
    fetch('/v1/security').then(r => r.json()),
  ]);

  $('env').textContent = `ambiente: ${status.environment} · enterprise: ${status.enterprise.id} · workspace: ${status.workspace}`;
  $('updated').textContent = 'atualizado ' + new Date().toLocaleTimeString('pt-BR');

  const c = status.counts;
  const cards = [
    ['agentes', c.agents], ['ferramentas', c.tools], ['políticas', c.policies],
    ['tasks', Object.values(c.tasks).reduce((a,b)=>a+b,0)],
    ['aprovações', c.approvals_pending], ['artefatos', c.artifacts],
    ['memória', c.memory.total], ['eventos', c.events],
  ];
  $('cards').innerHTML = cards.map(([k,v]) => `<div class="card"><span class="muted">${k}</span><b>${v}</b></div>`).join('');

  const secCards = [
    ['principais', sec.identities.active + '/' + sec.identities.total],
    ['tokens ativos', sec.identities.tokens_active],
    ['segredos', sec.vault.secrets],
    ['chave mestra', sec.vault.master_key.key_id || 'ausente'],
    ['identidade exigida', sec.identity_required ? 'sim' : 'não'],
  ];
  $('seccards').innerHTML = secCards.map(([k,v]) => `<div class="card"><span class="muted">${k}</span><b>${esc(v)}</b></div>`).join('');
  $('gaps').innerHTML = '<tr><th>lacunas de segurança</th></tr>' +
    ((sec.gaps||[]).length ? sec.gaps.map(g => `<tr><td class="warn">${esc(g)}</td></tr>`).join('')
      : '<tr><td class="ok">nenhuma lacuna aberta</td></tr>');

  const cur = status.budget.currency || 'USD';
  $('costcards').innerHTML = [
    ['gasto hoje', usage.total_cost.toFixed(6)],
    ['chamadas', usage.calls],
    ['tokens in/out', `${usage.input_tokens}/${usage.output_tokens}`],
    ['latência média', `${usage.avg_latency_ms} ms`],
    ['limite/dia', status.budget.per_day === null ? 'sem limite' : status.budget.per_day],
    ['roteamento', status.routing],
  ].map(([k,v]) => `<div class="card"><span class="muted">${k}</span><b>${esc(v)}</b></div>`).join('');

  $('usage').innerHTML = '<tr><th>provider</th><th>chamadas</th><th>custo</th><th>tokens</th><th>latência</th></tr>' +
    (usage.by_provider.length ? usage.by_provider.map(u => `<tr><td>${esc(u.provider)}</td><td>${u.calls}</td><td>${u.cost.toFixed(6)} ${esc(cur)}</td><td>${u.input_tokens}/${u.output_tokens}</td><td>${Math.round(u.avg_latency||0)} ms</td></tr>`).join('')
      : '<tr><td colspan="5" class="muted">nenhuma chamada de modelo ainda</td></tr>');

  $('approvals').innerHTML = '<tr><th>ação</th><th>solicitado por</th><th>papel</th><th>decisão</th></tr>' +
    (approvals.length ? approvals.map(a => `<tr>
      <td><code>${esc(a.tool)}</code><br><span class="muted">${esc(a.reason)}</span></td>
      <td>${esc(a.requested_by)}</td><td>${esc(a.required_role || '-')}</td>
      <td><button onclick="decide('${a.id}','approve')">aprovar</button><button class="deny" onclick="decide('${a.id}','deny')">negar</button></td>
    </tr>`).join('') : '<tr><td colspan="4" class="muted">nenhuma aprovação pendente</td></tr>');

  $('tasks').innerHTML = '<tr><th>id</th><th>status</th><th>agente</th><th>objetivo</th><th>custo</th></tr>' +
    (tasks.length ? tasks.map(t => {
      const cls = t.status === 'completed' ? 'ok' : (t.status === 'failed' ? 'bad' : (t.status === 'requires_approval' ? 'warn' : ''));
      const cost = (t.result && t.result.cost !== undefined) ? t.result.cost.toFixed(6) : '0.000000';
      return `<tr><td><code>${esc(t.id)}</code></td><td class="${cls}">${esc(t.status)}</td><td>${esc(t.agent_id)}</td><td>${esc(t.objective)}</td><td>${cost}</td></tr>`;
    }).join('') : '<tr><td colspan="4" class="muted">nenhuma task ainda</td></tr>');

  $('events').innerHTML = '<tr><th>#</th><th>evento</th><th>ator</th><th>task</th><th>detalhe</th></tr>' +
    events.map(e => `<tr><td>${e.seq}</td><td>${esc(e.type)}</td><td>${esc(e.actor)}</td><td><code>${esc(e.task_id || '')}</code></td><td>${esc(JSON.stringify(e.payload)).slice(0,140)}</td></tr>`).join('');
}

async function decide(id, decision) {
  const res = await fetch(`/v1/approvals/${id}/decision`, {
    method: 'POST', headers: authHeaders(),
    body: JSON.stringify({decision, by: 'console'}),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    alert(`decisão recusada (HTTP ${res.status}): ${detail.detail || 'sem detalhe'}`);
  }
  load();
}

loadIdent();
load();
setInterval(load, 5000);
</script>
</body>
</html>
"""


def _chat_html() -> str:
    return """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>EGR Chat</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: #0b0f14; color: #d7e0ea; }
  header { padding: 14px 20px; border-bottom: 1px solid #1d2733; display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
  h1 { font-size: 15px; margin: 0; letter-spacing: .08em; text-transform: uppercase; }
  .muted { color: #7c8b9c; }
  main { padding: 16px 20px; display: grid; gap: 12px; max-width: 900px; }
  #log { background: #111823; border: 1px solid #1d2733; border-radius: 10px; padding: 12px; min-height: 320px; max-height: 60vh; overflow-y: auto; }
  .msg { margin: 0 0 10px; white-space: pre-wrap; word-break: break-word; }
  .msg b { display: block; font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: #7c8b9c; margin-bottom: 2px; }
  .msg.egr b { color: #93c5fd; }
  .msg.denied { color: #f87171; }
  .row { display: flex; gap: 8px; }
  input { flex: 1; background: #0e1520; border: 1px solid #1d2733; color: #d7e0ea; border-radius: 8px; padding: 10px; font: inherit; }
  button { background: #1d4ed8; color: white; border: 0; border-radius: 8px; padding: 10px 16px; cursor: pointer; font: inherit; }
  code { color: #93c5fd; }
</style>
</head>
<body>
<header>
  <h1>EGR Chat</h1>
  <span class="muted">canal web</span>
  <span class="muted">sessão <code id="who"></code></span>
  <span style="margin-left:auto"><a class="muted" href="/">console</a></span>
</header>
<main>
  <div id="log"></div>
  <form class="row" id="form">
    <input id="text" placeholder="escreva um objetivo (ou /ajuda)" autocomplete="off" autofocus />
    <button type="submit">enviar</button>
  </form>
  <p class="muted">
    Cada mensagem é uma task governada: política, orçamento, aprovação e trilha
    valem aqui como valem no Telegram. Sem pareamento, o Runtime responde com o
    código — <code>egr gateway pair web &lt;sessão&gt; --code &lt;código&gt; --role operator</code>.
  </p>
</main>
<script>
const log = document.getElementById('log');
let who = localStorage.getItem('egr_web_session');
if (!who) { who = 'web-' + Math.random().toString(16).slice(2, 10); localStorage.setItem('egr_web_session', who); }
document.getElementById('who').textContent = who;

function add(role, text, denied) {
  const div = document.createElement('div');
  div.className = 'msg ' + role + (denied ? ' denied' : '');
  const b = document.createElement('b');
  b.textContent = role === 'eu' ? 'você' : (denied ? 'runtime (recusado)' : 'runtime');
  div.appendChild(b);
  div.appendChild(document.createTextNode(text));
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function send(text) {
  add('eu', text);
  const response = await fetch('/v1/gateway/messages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel: 'web', external_id: who, text }),
  });
  const data = await response.json();
  add('egr', data.texto + (data.task && data.task !== '-' ? '\\n(task ' + data.task + ')' : ''), data.recusada);
}

document.getElementById('form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = document.getElementById('text');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  try { await send(text); } catch (error) { add('egr', 'erro: ' + error, true); }
});

add('egr', 'canal web pronto. /ajuda lista os comandos.');
</script>
</body>
</html>
"""
