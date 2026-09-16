"""Camada de segurança: fronteira de dados, redação, identidade, RBAC e cofre.

    dados -> classificação -> minimização -> sanitização -> política -> mundo
    humano -> identidade verificável -> RBAC -> aprovação -> auditoria
"""

from .crypto import ENVELOPE_VERSION, decrypt_text, encrypt, key_id
from .data_boundary import BoundaryResult, check_external, classify, sanitize, sanitize_payload
from .identity import (
    IdentityService,
    IdentityToken,
    Principal,
    PrincipalKind,
    PrincipalStatus,
    parse_token,
)
from .keystore import ENV_VAR as MASTER_KEY_ENV
from .keystore import MasterKey, MasterKeyStore
from .rbac import ROLE_PERMISSIONS, ROLES, has_permission, permissions_for, role_satisfies
from .rbac import describe as describe_rbac
from .rbac import require as require_permission
from .redaction import redact_mapping, redact_text
from .secrets import load_dotenv, mask, resolve_secret
from .vault import VAULT_PREFIX, SecretRecord, SecretVault

__all__ = [
    "ENVELOPE_VERSION",
    "MASTER_KEY_ENV",
    "ROLES",
    "ROLE_PERMISSIONS",
    "VAULT_PREFIX",
    "BoundaryResult",
    "IdentityService",
    "IdentityToken",
    "MasterKey",
    "MasterKeyStore",
    "Principal",
    "PrincipalKind",
    "PrincipalStatus",
    "SecretRecord",
    "SecretVault",
    "check_external",
    "classify",
    "decrypt_text",
    "describe_rbac",
    "encrypt",
    "has_permission",
    "key_id",
    "load_dotenv",
    "mask",
    "parse_token",
    "permissions_for",
    "redact_mapping",
    "redact_text",
    "require_permission",
    "resolve_secret",
    "role_satisfies",
    "sanitize",
    "sanitize_payload",
]
