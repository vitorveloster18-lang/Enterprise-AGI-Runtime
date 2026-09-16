"""Identidade verificável: humanos, agentes e serviços com credencial própria.

Sem identidade, "aprovação humana" é só uma string num campo. Aqui ela vira um
objeto verificável:

    Principal(id, kind, roles) --issue_token--> egr_<token_id>.<segredo>

* O **segredo** nunca é armazenado: só `sha256(salt || segredo)`, comparado em
  tempo constante (`hmac.compare_digest`).
* O **token** carrega o `token_id` em claro para permitir revogação pontual.
* `Principal.kind == agent` **nunca** recebe `approval.decide`: um agente não
  aprova o próprio trabalho (RBAC + checagem explícita em
  `Runtime._authorize_decision`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from ..core.errors import AuthenticationError, AuthorizationError
from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.enums import BaseStrEnum, EventType
from .rbac import DEFAULT_ROLE, ROLES, permissions_for

TOKEN_PREFIX = "egr"
TOKEN_SECRET_BYTES = 32
DEFAULT_TTL_DAYS = 90


class PrincipalKind(BaseStrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"


class PrincipalStatus(BaseStrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Principal(BaseModel):
    """Quem pode agir no Runtime — e com quais papéis."""

    id: str
    name: str = ""
    kind: PrincipalKind = PrincipalKind.HUMAN
    roles: list[str] = Field(default_factory=lambda: [DEFAULT_ROLE])
    status: PrincipalStatus = PrincipalStatus.ACTIVE
    email: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status == PrincipalStatus.ACTIVE

    @property
    def permissions(self) -> list[str]:
        return sorted(permissions_for(self.roles))

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "kind": str(self.kind),
            "roles": sorted(self.roles),
            "status": str(self.status),
            "permissions": self.permissions,
        }


class IdentityToken(BaseModel):
    """Registro público do token: **nunca** guarda o segredo em claro."""

    id: str
    principal_id: str
    label: str = ""
    token_hash: str
    salt: str
    status: str = "active"  # active | revoked
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    @property
    def revoked(self) -> bool:
        return self.status == "revoked" or self.revoked_at is not None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= utcnow()

    @property
    def usable(self) -> bool:
        return not self.revoked and not self.expired

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "principal_id": self.principal_id,
            "label": self.label,
            "status": "revoked" if self.revoked else ("expired" if self.expired else "active"),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


def hash_token(secret: str, salt: bytes) -> str:
    return hashlib.sha256(salt + secret.encode("utf-8")).hexdigest()


def verify_token(secret: str, salt: bytes, expected: str) -> bool:
    return hmac.compare_digest(hash_token(secret, salt), expected)


def parse_token(raw: str) -> tuple[str, str]:
    """`egr_<token_id>.<segredo>` -> (token_id, segredo)."""

    token = (raw or "").strip()
    prefix = f"{TOKEN_PREFIX}_"
    if not token.startswith(prefix):
        raise AuthenticationError("token inválido: esperado o prefixo 'egr_'")
    body = token[len(prefix) :]
    token_id, separator, secret = body.partition(".")
    if not separator or not token_id or not secret:
        raise AuthenticationError("token malformado: esperado egr_<id>.<segredo>")
    return token_id, secret


class IdentityService:
    """Cria, credencia e autentica principais."""

    def __init__(self, repository, audit=None):
        self.repository = repository
        self.audit = audit

    # ---- principals ---------------------------------------------------
    def create_principal(
        self,
        principal_id: str,
        *,
        name: str = "",
        kind: str | PrincipalKind = PrincipalKind.HUMAN,
        roles: list[str] | None = None,
        email: str | None = None,
        actor: str = "cli",
        metadata: dict | None = None,
    ) -> Principal:
        if self.repository.get_principal(principal_id) is not None:
            raise AuthorizationError(f"principal '{principal_id}' já existe")
        unknown = [role for role in (roles or []) if role not in ROLES]
        if unknown:
            raise AuthorizationError(f"papéis desconhecidos: {', '.join(unknown)} (válidos: {', '.join(ROLES)})")
        principal = Principal(
            id=principal_id,
            name=name or principal_id,
            kind=PrincipalKind(kind),
            roles=sorted(set(roles or [DEFAULT_ROLE])),
            email=email,
            metadata=metadata or {},
        )
        self.repository.save_principal(principal)
        self._record(
            EventType.IDENTITY_CREATED,
            actor=actor,
            principal=principal,
            payload={"kind": str(principal.kind), "roles": principal.roles},
        )
        return principal

    def get(self, principal_id: str) -> Principal | None:
        return self.repository.get_principal(principal_id)

    def list(self, kind: str | None = None, status: str | None = None) -> list[Principal]:
        return self.repository.list_principals(kind=kind, status=status)

    def count(self) -> int:
        return self.repository.count_principals()

    def set_roles(self, principal_id: str, roles: list[str], *, actor: str = "cli") -> Principal:
        principal = self._require_principal(principal_id)
        unknown = [role for role in roles if role not in ROLES]
        if unknown:
            raise AuthorizationError(f"papéis desconhecidos: {', '.join(unknown)}")
        principal.roles = sorted(set(roles)) or [DEFAULT_ROLE]
        principal.updated_at = utcnow()
        self.repository.save_principal(principal)
        self._record(
            EventType.IDENTITY_UPDATED,
            actor=actor,
            principal=principal,
            payload={"roles": principal.roles},
        )
        return principal

    def set_status(self, principal_id: str, status: str, *, actor: str = "cli") -> Principal:
        principal = self._require_principal(principal_id)
        principal.status = PrincipalStatus(status)
        principal.updated_at = utcnow()
        self.repository.save_principal(principal)
        self._record(
            EventType.IDENTITY_DISABLED if not principal.active else EventType.IDENTITY_UPDATED,
            actor=actor,
            principal=principal,
            payload={"status": str(principal.status)},
        )
        return principal

    def remove(self, principal_id: str, *, actor: str = "cli") -> bool:
        return self.repository.delete_principal(principal_id)

    # ---- tokens -------------------------------------------------------
    def issue_token(
        self,
        principal_id: str,
        *,
        ttl_days: int = DEFAULT_TTL_DAYS,
        label: str = "",
        actor: str = "cli",
    ) -> tuple[str, IdentityToken]:
        """Devolve o token em claro **uma única vez**; só o hash é persistido."""

        principal = self._require_principal(principal_id)
        secret = secrets.token_urlsafe(TOKEN_SECRET_BYTES)
        salt = secrets.token_bytes(16)
        token_id = new_id("token")
        record = IdentityToken(
            id=token_id,
            principal_id=principal.id,
            label=label,
            token_hash=hash_token(secret, salt),
            salt=salt.hex(),
            expires_at=utcnow() + timedelta(days=ttl_days) if ttl_days and ttl_days > 0 else None,
        )
        self.repository.save_token(record)
        self._record(
            EventType.TOKEN_ISSUED,
            actor=actor,
            principal=principal,
            payload={
                "token": token_id,
                "label": label,
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            },
        )
        return f"{TOKEN_PREFIX}_{token_id}.{secret}", record

    def revoke_token(self, token_id: str, *, actor: str = "cli") -> IdentityToken:
        record = self.repository.get_token(token_id)
        if record is None:
            raise AuthenticationError(f"token '{token_id}' não encontrado")
        record.status = "revoked"
        record.revoked_at = utcnow()
        self.repository.save_token(record)
        self._record(
            EventType.TOKEN_REVOKED,
            actor=actor,
            principal_id=record.principal_id,
            payload={"token": record.id},
        )
        return record

    def tokens(self, principal_id: str | None = None) -> list[IdentityToken]:
        return self.repository.list_tokens(principal_id=principal_id)

    # ---- autenticação -------------------------------------------------
    def authenticate(self, raw: str) -> Principal:
        """Autentica um token. Falha fechada: qualquer dúvida é erro."""

        token_id, secret = parse_token(raw)
        record = self.repository.get_token(token_id)
        if record is None:
            raise AuthenticationError("token desconhecido")
        if not verify_token(secret, bytes.fromhex(record.salt), record.token_hash):
            self._record(EventType.AUTH_FAILED, actor=token_id, payload={"token": token_id, "reason": "bad_secret"})
            raise AuthenticationError("token inválido")
        if record.revoked:
            self._record(EventType.AUTH_FAILED, actor=token_id, payload={"token": token_id, "reason": "revoked"})
            raise AuthenticationError("token revogado")
        if record.expired:
            self._record(EventType.AUTH_FAILED, actor=token_id, payload={"token": token_id, "reason": "expired"})
            raise AuthenticationError("token expirado")

        principal = self.repository.get_principal(record.principal_id)
        if principal is None:
            raise AuthenticationError(f"token órfão: principal '{record.principal_id}' não existe")
        if not principal.active:
            self._record(
                EventType.AUTH_FAILED,
                actor=principal.id,
                payload={"token": token_id, "reason": "principal_disabled"},
            )
            raise AuthenticationError(f"principal '{principal.id}' está desabilitado")

        record.last_used_at = utcnow()
        self.repository.save_token(record)
        principal.last_seen_at = record.last_used_at
        self.repository.save_principal(principal)
        self._record(
            EventType.AUTH_SUCCEEDED,
            actor=principal.id,
            payload={"token": token_id, "roles": principal.roles},
        )
        return principal

    def resolve(self, actor: str | None) -> Principal | None:
        """Aceita um token (`egr_...`) ou um id de principal. Nunca levanta."""

        if not actor:
            return None
        if actor.startswith(f"{TOKEN_PREFIX}_"):
            try:
                return self.authenticate(actor)
            except AuthenticationError:
                return None
        principal = self.repository.get_principal(actor)
        return principal if principal and principal.active else None

    def whoami(self, actor: str | None) -> dict:
        principal = self.resolve(actor)
        if principal is None:
            return {"authenticated": False, "actor": actor}
        return {"authenticated": True, **principal.as_row()}

    # ---- helpers ------------------------------------------------------
    def _require_principal(self, principal_id: str) -> Principal:
        principal = self.repository.get_principal(principal_id)
        if principal is None:
            raise AuthenticationError(f"principal '{principal_id}' não encontrado")
        return principal

    def _record(
        self,
        event_type: EventType | str,
        *,
        actor: str,
        payload: dict | None = None,
        principal: Principal | None = None,
        principal_id: str | None = None,
    ) -> None:
        if self.audit is None:
            return
        self.audit.record(
            event_type,
            actor=actor,
            agent_id=principal.id if principal and principal.kind == PrincipalKind.AGENT else None,
            payload=payload or {},
        )


__all__ = [
    "DEFAULT_TTL_DAYS",
    "TOKEN_PREFIX",
    "IdentityService",
    "IdentityToken",
    "Principal",
    "PrincipalKind",
    "PrincipalStatus",
    "hash_token",
    "parse_token",
    "verify_token",
]
