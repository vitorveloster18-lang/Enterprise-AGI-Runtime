"""Secrets never live in config files or in the database.

Resolution order for a reference:
  1. `vault:NOME`   -> cofre cifrado do Runtime (Fase 4)
  2. `env:VARIAVEL` -> variável de ambiente explícita
  3. `VARIAVEL`     -> variável de ambiente (compatível com `api_key_env`)
  4. arquivo `.env` opcional na raiz do workspace (nunca versionado)
"""

from __future__ import annotations

import os
from pathlib import Path

from .redaction import REDACTED
from .vault import VAULT_PREFIX, SecretVault


def load_dotenv(path: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
        loaded[key] = value
    return loaded


def resolve_secret(api_key_env: str | None, vault: SecretVault | None = None) -> str | None:
    """Resolve uma referência de segredo.

    `vault:NOME` (cofre) tem precedência sobre ambiente: credencial de produção
    deve sair do cofre, não do ambiente do processo.
    """

    if not api_key_env:
        return None
    reference = api_key_env.strip()
    if reference.startswith(VAULT_PREFIX):
        if vault is None:
            return None
        return vault.resolve_reference(reference)
    if reference.startswith("env:"):
        return os.environ.get(reference[4:].strip())
    return os.environ.get(reference)


def mask(value: str | None) -> str:
    if not value:
        return REDACTED
    return f"{value[:2]}...{value[-2:]}" if len(value) > 8 else REDACTED
