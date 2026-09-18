"""RBAC real: papel → permissão, verificado no momento da decisão.

O modelo propõe; o Runtime governa. "O humano aprova o crítico" só é uma
afirmação verificável se existir uma resposta para **quem** aprovou e **com qual
autoridade**. Este módulo é essa resposta:

    Principal(roles) -> expand_roles() -> permissions_for() -> has_permission()

Papéis são aditivos e explícitos (sem herança implícita escondida). `admin`
satisfaz qualquer papel — inclusive o exigido por uma política
(`required_role`).
"""

from __future__ import annotations

from typing import Any

from ..core.errors import AuthorizationError

# ---- permissões -------------------------------------------------------
TASK_READ = "task.read"
TASK_SUBMIT = "task.submit"
TASK_CANCEL = "task.cancel"
TASK_HANDOFF = "task.handoff"      # lacuna 6b: repassar task para outro agente
TOOLS_EXECUTE = "tools.execute"
APPROVAL_READ = "approval.read"
APPROVAL_DECIDE = "approval.decide"
POLICY_READ = "policy.read"
POLICY_WRITE = "policy.write"
POLICY_SYNC = "policy.sync"
AGENT_READ = "agent.read"
AGENT_WRITE = "agent.write"
AGENT_PROMOTE = "agent.promote"
RELEASE_PROMOTE = "release.promote"
MEMORY_READ = "memory.read"
MEMORY_WRITE = "memory.write"
AUDIT_READ = "audit.read"
SECRET_READ = "secret.read"
SECRET_WRITE = "secret.write"
SECRET_ROTATE = "secret.rotate"
KEY_MANAGE = "key.manage"
IDENTITY_MANAGE = "identity.manage"
# Fase 10 — gateway de canais (Telegram/Slack/Web)
GATEWAY_USE = "gateway.use"
GATEWAY_PAIR = "gateway.pair"
# Fase 11 — integrações (REST/GraphQL/SQL/webhook)
INTEGRATION_CALL = "integration.call"
INTEGRATION_MANAGE = "integration.manage"

PERMISSIONS: tuple[str, ...] = (
    TASK_READ,
    TASK_SUBMIT,
    TASK_CANCEL,
    TOOLS_EXECUTE,
    APPROVAL_READ,
    APPROVAL_DECIDE,
    POLICY_READ,
    POLICY_WRITE,
    POLICY_SYNC,
    AGENT_READ,
    AGENT_WRITE,
    AGENT_PROMOTE,
    RELEASE_PROMOTE,
    MEMORY_READ,
    MEMORY_WRITE,
    AUDIT_READ,
    SECRET_READ,
    SECRET_WRITE,
    SECRET_ROTATE,
    KEY_MANAGE,
    IDENTITY_MANAGE,
    GATEWAY_USE,
    GATEWAY_PAIR,
    INTEGRATION_CALL,
    INTEGRATION_MANAGE,
)

# Fase 10: falar com o Runtime por um canal não é executar — executar continua sendo task.submit
_VIEWER = {TASK_READ, POLICY_READ, AGENT_READ, APPROVAL_READ, MEMORY_READ, AUDIT_READ, GATEWAY_USE}
_OPERATOR = _VIEWER | {
    TASK_SUBMIT,
    TASK_CANCEL,
    TASK_HANDOFF,      # lacuna 6b: repassar task é ato de operação, com motivo
    TOOLS_EXECUTE,
    MEMORY_WRITE,
    POLICY_SYNC,
    RELEASE_PROMOTE,
    INTEGRATION_CALL,
}
_APPROVER = _VIEWER | {APPROVAL_DECIDE, RELEASE_PROMOTE, GATEWAY_PAIR}
_AUDITOR = _VIEWER | {AUDIT_READ}
_SECURITY_ADMIN = _OPERATOR | {
    SECRET_READ,
    SECRET_WRITE,
    SECRET_ROTATE,
    KEY_MANAGE,
    IDENTITY_MANAGE,
    POLICY_WRITE,
    INTEGRATION_MANAGE,
}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset(_VIEWER),
    "operator": frozenset(_OPERATOR),
    "approver": frozenset(_APPROVER),
    "auditor": frozenset(_AUDITOR),
    "security_admin": frozenset(_SECURITY_ADMIN),
    "admin": frozenset(PERMISSIONS),
}

#: `admin` satisfaz tudo; os demais papéis herdam explicitamente.
ROLE_INHERITS: dict[str, tuple[str, ...]] = {
    "admin": ("security_admin", "approver", "operator", "auditor", "viewer"),
    "security_admin": ("operator", "viewer"),
    "approver": ("operator", "viewer"),
    "auditor": ("viewer",),
    "operator": ("viewer",),
    "viewer": (),
}

DEFAULT_ROLE = "viewer"
ROLES: tuple[str, ...] = ("viewer", "operator", "approver", "auditor", "security_admin", "admin")


def expand_roles(roles: list[str] | tuple[str, ...] | set[str] | None) -> set[str]:
    """Fecha transitivamente os papéis herdados."""

    expanded: set[str] = set()
    pending = list(roles or [])
    while pending:
        role = pending.pop()
        if role in expanded:
            continue
        expanded.add(role)
        pending.extend(role for role in ROLE_INHERITS.get(role, ()) if role not in expanded)
    return expanded


def permissions_for(roles: list[str] | None) -> set[str]:
    granted: set[str] = set()
    for role in expand_roles(roles):
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return granted


def has_permission(principal_or_roles: Any, permission: str) -> bool:
    roles = _roles_of(principal_or_roles)
    return permission in permissions_for(roles)


def require(principal: Any, permission: str, *, context: str = "") -> None:
    """Falha fechada: sem permissão, a ação não acontece."""

    if not has_permission(principal, permission):
        roles = sorted(_roles_of(principal)) or ["-"]
        raise AuthorizationError(
            f"'{_id_of(principal)}' (papéis: {', '.join(roles)}) não tem a permissão '{permission}'"
            + (f" — {context}" if context else "")
        )


def role_satisfies(held: list[str] | None, required: str | None) -> bool:
    """Um papel exigido por política é satisfeito por ele mesmo ou por `admin`."""

    if not required:
        return True
    return required in expand_roles(held)


def describe() -> dict:
    return {
        "roles": [
            {"role": role, "permissions": sorted(ROLE_PERMISSIONS[role]), "inherits": list(ROLE_INHERITS[role])}
            for role in ROLES
        ],
        "permissions": list(PERMISSIONS),
    }


def _roles_of(principal_or_roles: Any) -> list[str]:
    if principal_or_roles is None:
        return []
    if isinstance(principal_or_roles, str):
        return [principal_or_roles]
    if isinstance(principal_or_roles, list | tuple | set):
        return list(principal_or_roles)
    return list(getattr(principal_or_roles, "roles", []) or [])


def _id_of(principal: Any) -> str:
    return str(getattr(principal, "id", principal))


__all__ = [
    "AGENT_PROMOTE",
    "AGENT_READ",
    "AGENT_WRITE",
    "APPROVAL_DECIDE",
    "APPROVAL_READ",
    "AUDIT_READ",
    "DEFAULT_ROLE",
    "IDENTITY_MANAGE",
    "KEY_MANAGE",
    "MEMORY_READ",
    "MEMORY_WRITE",
    "PERMISSIONS",
    "POLICY_READ",
    "POLICY_SYNC",
    "POLICY_WRITE",
    "ROLES",
    "ROLE_INHERITS",
    "ROLE_PERMISSIONS",
    "SECRET_READ",
    "SECRET_ROTATE",
    "SECRET_WRITE",
    "TASK_CANCEL",
    "TASK_READ",
    "TASK_SUBMIT",
    "TOOLS_EXECUTE",
    "describe",
    "expand_roles",
    "has_permission",
    "permissions_for",
    "require",
    "role_satisfies",
]
