"""Fundamental enums of the Runtime."""

from __future__ import annotations

from enum import StrEnum


class BaseStrEnum(StrEnum):
    """String enum: JSON and f-strings render the value, never the name."""

    @classmethod
    def values(cls) -> list[str]:
        return [member.value for member in cls]


class Environment(BaseStrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class TaskStatus(BaseStrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REQUIRES_APPROVAL = "requires_approval"


class DecisionType(BaseStrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class EventType(BaseStrEnum):
    # task lifecycle
    TASK_CREATED = "task.created"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    TASK_WAITING = "task.waiting"
    TASK_RESUMED = "task.resumed"
    TASK_DELEGATED = "task.delegated"
    # agent / planning
    AGENT_LOADED = "agent.loaded"
    PLAN_CREATED = "plan.created"
    PLAN_FAILED = "plan.failed"
    ACTION_PROPOSED = "action.proposed"
    # governance
    POLICY_ALLOWED = "policy.allowed"
    POLICY_DENIED = "policy.denied"
    POLICY_APPROVAL_REQUIRED = "policy.approval_required"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    HUMAN_DECISION = "human.decision"
    # supervisão (fatia 3: orquestrador revisa o subagente)
    SUPERVISION_REVIEW = "supervision.review"
    # compliance (fatia 4: a exportação da trilha também é auditada)
    AUDIT_EXPORTED = "audit.exported"
    # execution
    TOOL_EXECUTED = "tool.executed"
    TOOL_FAILED = "tool.failed"
    # intelligence
    MODEL_CALLED = "model.called"
    MODEL_FAILED = "model.failed"
    MODEL_BUDGET_BLOCKED = "model.budget_blocked"
    # extensões cognitivas (fatia 6: overflow de contexto e escalação)
    MODEL_OVERFLOW = "model.overflow"
    # memory / artifacts
    MEMORY_WRITTEN = "memory.written"
    # lacuna 5b: memória multimodal e limpeza de PII na escrita
    MEMORY_MEDIA_WRITTEN = "memory.media_written"
    MEMORY_PII_SCRUBBED = "memory.pii_scrubbed"
    MEMORY_RECALLED = "memory.recalled"
    ARTIFACT_CREATED = "artifact.created"
    # data boundary
    DATA_CLASSIFIED = "data.classified"
    DATA_SANITIZED = "data.sanitized"
    DATA_BLOCKED = "data.blocked"
    # security: identity, keys and secrets (Fase 4)
    IDENTITY_CREATED = "identity.created"
    IDENTITY_UPDATED = "identity.updated"
    IDENTITY_DISABLED = "identity.disabled"
    TOKEN_ISSUED = "identity.token_issued"
    TOKEN_REVOKED = "identity.token_revoked"
    AUTH_SUCCEEDED = "security.auth_succeeded"
    AUTH_FAILED = "security.auth_failed"
    # SSO (fatia 5: humano autenticado pelo IdP da empresa)
    SSO_AUTHENTICATED = "sso.authenticated"
    AUTHORIZATION_DENIED = "security.authorization_denied"
    SECRET_STORED = "secret.stored"
    SECRET_ROTATED = "secret.rotated"
    SECRET_REMOVED = "secret.removed"
    KEY_INITIALIZED = "key.initialized"
    KEY_ROTATED = "key.rotated"
    # desenvolvimento (Fase 7): o Runtime estendendo a si mesmo sob proposta
    DEV_PROPOSAL_CREATED = "dev.proposal_created"
    DEV_PROPOSAL_VALIDATED = "dev.proposal_validated"
    DEV_PROPOSAL_FAILED = "dev.proposal_failed"
    DEV_PROPOSAL_TESTED = "dev.proposal_tested"
    DEV_PROPOSAL_APPROVED = "dev.proposal_approved"
    DEV_PROPOSAL_REJECTED = "dev.proposal_rejected"
    DEV_PROPOSAL_APPLIED = "dev.proposal_applied"
    DEV_TOOL_LOADED = "dev.tool_loaded"
    DEV_TOOL_REJECTED = "dev.tool_rejected"
    # avaliação (Fase 8): provar qualidade, custo, latência e segurança
    EVAL_SUITE_CREATED = "eval.suite_created"
    EVAL_RUN_STARTED = "eval.run_started"
    EVAL_RUN_FINISHED = "eval.run_finished"
    EVAL_REGRESSION = "eval.regression"
    EVAL_SECURITY_FINDING = "eval.security_finding"
    # lacuna 8b: laboratório — qualidade de modelo, comparação e carga
    EVAL_JUDGED = "eval.judged"
    EVAL_COMPARED = "eval.compared"
    EVAL_LOAD_FINISHED = "eval.load_finished"
    # governança (Fase 9): promoção entre ambientes, versionamento e rollback
    RELEASE_CREATED = "release.created"
    RELEASE_SUBMITTED = "release.submitted"
    RELEASE_APPROVED = "release.approved"
    RELEASE_REJECTED = "release.rejected"
    RELEASE_DEPLOYED = "release.deployed"
    RELEASE_ROLLED_BACK = "release.rolled_back"
    # lacuna 9b: assinatura criptográfica e quórum de aprovação
    RELEASE_SIGNED = "release.signed"
    RELEASE_APPROVAL_RECORDED = "release.approval_recorded"
    # lacuna 6b: coordenação negociada (lances, handoff) e gatilhos de banco
    TASK_NEGOTIATED = "task.negotiated"
    TASK_HANDOFF = "task.handoff"
    DB_TRIGGER_INSTALLED = "db.trigger_installed"
    DB_TRIGGER_FIRED = "db.trigger_fired"
    ARTIFACT_VERSIONED = "artifact.versioned"
    # gateway de canais (Fase 10): Telegram/Slack/Web são interfaces, não núcleo
    GATEWAY_MESSAGE_RECEIVED = "gateway.message_received"
    GATEWAY_MESSAGE_SENT = "gateway.message_sent"
    GATEWAY_DENIED = "gateway.denied"
    GATEWAY_PAIRED = "gateway.paired"
    GATEWAY_UNPAIRED = "gateway.unpaired"
    # integrações (Fase 11): sistema externo é fronteira, não extensão do agente
    INTEGRATION_CALLED = "integration.called"
    INTEGRATION_DENIED = "integration.denied"
    INTEGRATION_TESTED = "integration.tested"
    INTEGRATION_EVENT_RECEIVED = "integration.event_received"
    INTEGRATION_EVENT_REJECTED = "integration.event_rejected"
    # pacotes verticais (Fase 12): instalados por proposta, nunca por atalho
    # fila de saída (Fase 12): sistema alheio falha, o Runtime não esquece
    INTEGRATION_JOB_QUEUED = "integration.job_queued"
    INTEGRATION_JOB_FAILED = "integration.job_failed"
    PACK_INSTALLED = "pack.installed"
    PACK_UPDATED = "pack.updated"
    PACK_REMOVED = "pack.removed"
    PACK_DENIED = "pack.denied"
    # lacuna 10b: anexo é conteúdo governado; botão é atalho para um comando
    GATEWAY_ATTACHMENT = "gateway.attachment"
    GATEWAY_ATTACHMENT_REJECTED = "gateway.attachment_rejected"
    GATEWAY_INTERACTION = "gateway.interaction"
    # system
    SYSTEM_EVENT = "system.event"


class ChannelKind(BaseStrEnum):
    """Fase 10: por onde a mensagem chega — nunca muda quem governa."""

    CONSOLE = "console"
    TELEGRAM = "telegram"
    SLACK = "slack"
    WEB = "web"


class IntegrationKind(BaseStrEnum):
    """Fase 11: conector declarado (REST, GraphQL, SQL, webhook de entrada)."""

    REST = "rest"
    GRAPHQL = "graphql"
    SQL = "sql"
    WEBHOOK = "webhook"


class IntegrationEventStatus(BaseStrEnum):
    """O que o Runtime fez com um evento que chegou de fora."""

    RECEIVED = "received"      # aceito e registrado
    DUPLICATE = "duplicate"    # id repetido: idempotência
    REJECTED = "rejected"      # assinatura inválida ou conector desabilitado
    TRIGGERED = "triggered"    # virou trabalho (gatilho de workflow)


class BindingStatus(BaseStrEnum):
    """Pareamento entre um remetente externo e um Principal do Runtime."""

    PENDING = "pending"    # apareceu, mas ninguém autorizou
    ACTIVE = "active"      # pareado: fala com o Runtime conforme seus papéis
    BLOCKED = "blocked"    # recusado explicitamente


class AttachmentStatus(BaseStrEnum):
    """Lacuna 10b: o caminho de um anexo — aceito, guardado ou recusado.

    Recusar também é resultado: o remetente recebe o motivo e a trilha
    registra a tentativa. Nenhum arquivo entra em silêncio.
    """

    RECEIVED = "received"    # descrito pelo canal, ainda não guardado
    STORED = "stored"        # gravado no inbox do workspace, com checksum
    REJECTED = "rejected"    # tipo, tamanho ou origem fora do permitido


class MemoryModality(BaseStrEnum):
    """Lacuna 5b: o suporte do registro (o buscável é sempre o lado textual)."""

    TEXT = "texto"
    IMAGE = "imagem"
    AUDIO = "áudio"
    DOCUMENT = "documento"


class MemoryKind(BaseStrEnum):
    KNOWLEDGE = "knowledge"
    OPERATIONAL = "operational"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class RunStatus(BaseStrEnum):
    """Estado de uma execução de workflow (Fase 6)."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"    # pausada: aprovação humana pendente
    COMPLETED = "completed"
    PARTIAL = "partial"    # terminou, mas com passos tolerados como falhos
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepRunStatus(BaseStrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class JobStatus(BaseStrEnum):
    """Um job de saída: onde ele está na fila."""

    PENDING = "pending"      # esperando a vez (ou a espera crescente)
    RUNNING = "running"      # tentativa em andamento
    DONE = "done"            # chamada executada
    FAILED = "failed"        # esgotou as tentativas: motivo registrado
    CANCELLED = "cancelled"  # humano cancelou


class PackStatus(BaseStrEnum):
    """Situação de um pacote vertical neste workspace."""

    AVAILABLE = "available"   # existe no catálogo, não instalado
    PROPOSED = "proposed"     # proposta criada, esperando verificação/aprovação
    INSTALLED = "installed"   # aplicado: arquivos no workspace e registro no banco
    OUTDATED = "outdated"     # instalado em versão diferente da do catálogo


class ProposalKind(BaseStrEnum):
    """Artefatos que o Runtime pode criar sob proposta (Fase 7)."""

    AGENT = "agent"
    TOOL = "tool"
    WORKFLOW = "workflow"
    POLICY = "policy"
    PACK = "pack"


class ProposalStatus(BaseStrEnum):
    """Ciclo de vida de uma proposta: nada entra no workspace sem passar por aqui."""

    DRAFT = "draft"            # criada, ainda não validada
    VALIDATED = "validated"    # passou pelas verificações estáticas
    TESTED = "tested"          # executou no sandbox (ferramentas)
    APPROVED = "approved"      # humano aprovou
    APPLIED = "applied"        # escrita no workspace e carregada
    REJECTED = "rejected"      # humano recusou
    FAILED = "failed"          # reprovada nas verificações


class EvaluationTarget(BaseStrEnum):
    """O que pode ser avaliado (Fase 8)."""

    TOOL = "tool"
    WORKFLOW = "workflow"
    AGENT = "agent"
    POLICY = "policy"


class EvaluationStatus(BaseStrEnum):
    """Veredito de uma execução de avaliação."""

    PASSED = "passed"        # dentro de todos os limites
    FAILED = "failed"        # abaixo do mínimo aceitável
    REGRESSED = "regressed"  # piorou em relação à baseline
    ERROR = "error"          # nem deu para avaliar (alvo ausente/quebrado)


class ReleaseStatus(BaseStrEnum):
    """Ciclo de uma promoção entre ambientes (Fase 9)."""

    DRAFT = "draft"            # montada, ainda não conferida
    SUBMITTED = "submitted"    # conferida e proposta ao humano
    APPROVED = "approved"      # humano aprovou
    DEPLOYED = "deployed"      # aplicada no ambiente de destino
    REJECTED = "rejected"      # humano recusou
    ROLLED_BACK = "rolled_back"  # revertida depois de aplicada
    FAILED = "failed"          # reprovada nos gates


class FindingSeverity(BaseStrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ApprovalStatus(BaseStrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    EXECUTED = "executed"


class ArtifactKind(BaseStrEnum):
    REPORT = "report"
    FILE = "file"
    PLAN = "plan"
    AGENT = "agent"
    TOOL = "tool"
    WORKFLOW = "workflow"
    POLICY = "policy"
    PACK = "pack"
    EVALUATION = "evaluation"
    PROPOSAL = "proposal"


class RiskLevel(BaseStrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
