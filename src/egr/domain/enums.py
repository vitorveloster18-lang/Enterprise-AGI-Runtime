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
    # execution
    TOOL_EXECUTED = "tool.executed"
    TOOL_FAILED = "tool.failed"
    # intelligence
    MODEL_CALLED = "model.called"
    MODEL_FAILED = "model.failed"
    MODEL_BUDGET_BLOCKED = "model.budget_blocked"
    # memory / artifacts
    MEMORY_WRITTEN = "memory.written"
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
    AUTHORIZATION_DENIED = "security.authorization_denied"
    SECRET_STORED = "secret.stored"
    SECRET_ROTATED = "secret.rotated"
    SECRET_REMOVED = "secret.removed"
    KEY_INITIALIZED = "key.initialized"
    KEY_ROTATED = "key.rotated"
    # system
    SYSTEM_EVENT = "system.event"


class MemoryKind(BaseStrEnum):
    KNOWLEDGE = "knowledge"
    OPERATIONAL = "operational"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


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
    EVALUATION = "evaluation"
    PROPOSAL = "proposal"


class RiskLevel(BaseStrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
