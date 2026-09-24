"""Costura de SSO: o Runtime não é um IdP — ele aceita o IdP da empresa.

O fluxo é propositalmente estreito:

* o gateway/IdP da empresa emite um JWT (HS256) para o humano já autenticado;
* `SSOVerifier` confere assinatura, `iss`/`aud` e expiração (só stdlib);
* grupos do claim viram papéis e áreas via `role_map`/`area_map`;
* o principal é provisionado/atualizado no primeiro login (o IdP manda).

O segredo nunca vai para o `egr.yaml`: `secret_env` nomeia a variável de
ambiente lida a cada verificação (permite rotação sem reiniciar). IdPs
RS256/JWKS entram pela mesma costura trocando o verificador — a interface
é `verify(raw) -> SSOClaims`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from pydantic import BaseModel, Field

from ..core.errors import AuthenticationError, ConfigError
from ..core.timeutil import utcnow
from .rbac import DEFAULT_ROLE, ROLES


def is_jwt(raw: str | None) -> bool:
    """Três segmentos base64 separados por ponto (e não é token `egr_`)."""
    if not raw or raw.startswith("egr_") or " " in raw:
        return False
    parts = raw.split(".")
    return len(parts) == 3 and all(parts)


def _b64url_decode(segment: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except Exception as exc:
        raise AuthenticationError("sso: token malformado") from exc


def verify_jwt_hs256(token: str, secret: str, *, issuer: str, audience: str) -> dict:
    """Confere um JWT HS256 com stdlib. Falha fechada: qualquer dúvida é erro."""

    parts = (token or "").split(".")
    if len(parts) != 3 or not all(parts):
        raise AuthenticationError("sso: token malformado")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError("sso: token malformado") from exc
    if header.get("alg") != "HS256":
        raise AuthenticationError("sso: algoritmo inesperado (esperado HS256)")
    expected = hmac.new(
        secret.encode("utf-8"), f"{header_b64}.{payload_b64}".encode("ascii"), hashlib.sha256
    ).digest()
    try:
        signature = _b64url_decode(signature_b64)
    except AuthenticationError:
        raise AuthenticationError("sso: assinatura inválida") from None
    if not hmac.compare_digest(signature, expected):
        raise AuthenticationError("sso: assinatura inválida")
    now = int(time.time())
    if "exp" not in payload:
        raise AuthenticationError("sso: claim 'exp' ausente")
    try:
        expired = int(payload["exp"]) <= now
    except (TypeError, ValueError) as exc:
        raise AuthenticationError("sso: claim 'exp' inválido") from exc
    if expired:
        raise AuthenticationError("sso: token expirado")
    if "nbf" in payload:
        try:
            active = int(payload["nbf"]) <= now
        except (TypeError, ValueError) as exc:
            raise AuthenticationError("sso: claim 'nbf' inválido") from exc
        if not active:
            raise AuthenticationError("sso: token ainda não vale")
    if issuer and payload.get("iss") != issuer:
        raise AuthenticationError("sso: emissor inesperado")
    if audience:
        aud = payload.get("aud")
        audiences = aud if isinstance(aud, list) else [aud]
        if audience not in audiences:
            raise AuthenticationError("sso: audiência inesperada")
    if not payload.get("sub"):
        raise AuthenticationError("sso: claim 'sub' ausente")
    return payload


class SSOConfig(BaseModel):
    """Como o Runtime aceita o IdP da empresa. Desligado por padrão."""

    enabled: bool = False
    issuer: str = ""
    audience: str = ""
    secret_env: str = "EGR_SSO_SECRET"
    groups_claim: str = "groups"
    email_claim: str = "email"
    name_claim: str = "name"
    role_map: dict[str, list[str]] = Field(default_factory=dict)
    area_map: dict[str, list[str]] = Field(default_factory=dict)
    default_roles: list[str] = Field(default_factory=lambda: [DEFAULT_ROLE])


class SSOClaims(BaseModel):
    sub: str
    iss: str = ""
    aud: str | list[str] | None = None
    exp: int = 0
    email: str | None = None
    name: str | None = None
    groups: list[str] = Field(default_factory=list)


class SSOVerifier:
    """Verifica JWTs do IdP e traduz claims em identidade do Runtime."""

    def __init__(self, config: SSOConfig):
        self.config = config
        unknown_roles = sorted(
            {role for roles in config.role_map.values() for role in roles} - set(ROLES)
        )
        if unknown_roles:
            raise ConfigError(
                f"sso.role_map com papéis desconhecidos: {', '.join(unknown_roles)} "
                f"(válidos: {', '.join(ROLES)})"
            )
        unknown_defaults = [r for r in config.default_roles if r not in ROLES]
        if unknown_defaults:
            raise ConfigError(f"sso.default_roles com papéis desconhecidos: {unknown_defaults}")

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def verify(self, raw: str) -> SSOClaims:
        """Valida o JWT e devolve os claims. Nunca devolve parcial."""
        secret = os.environ.get(self.config.secret_env, "")
        if not secret:
            raise AuthenticationError(f"sso: segredo ausente (env {self.config.secret_env})")
        payload = verify_jwt_hs256(
            raw, secret, issuer=self.config.issuer, audience=self.config.audience
        )
        groups = payload.get(self.config.groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        return SSOClaims(
            sub=str(payload["sub"]),
            iss=str(payload.get("iss") or ""),
            aud=payload.get("aud"),
            exp=int(payload["exp"]),
            email=payload.get(self.config.email_claim),
            name=payload.get(self.config.name_claim),
            groups=[str(g) for g in groups],
        )

    def principal_spec(self, claims: SSOClaims) -> dict:
        """Traduz claims em (id, nome, e-mail, papéis, áreas, metadados)."""
        roles = sorted(
            {role for group in claims.groups for role in self.config.role_map.get(group, [])}
        ) or sorted(set(self.config.default_roles))
        areas = sorted(
            {area for group in claims.groups for area in self.config.area_map.get(group, [])}
        )
        principal_id = claims.email or f"sso:{claims.sub}"
        return {
            "id": principal_id,
            "name": claims.name or principal_id,
            "email": claims.email,
            "roles": roles,
            "areas": areas,
            "metadata": {
                "sso": {"sub": claims.sub, "iss": claims.iss, "synced_at": utcnow().isoformat()}
            },
        }


__all__ = [
    "SSOClaims",
    "SSOConfig",
    "SSOVerifier",
    "is_jwt",
    "verify_jwt_hs256",
]
