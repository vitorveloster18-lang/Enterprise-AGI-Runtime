"""Gestão da chave mestra do cofre.

    EGR_MASTER_KEY (ambiente)  ─┐
                                ├─> MasterKeyStore -> chave de 32 bytes
    <workspace>/.egr/master.key ┘

A chave **nunca** aparece em `egr.yaml`, no banco, em log ou na auditoria: só o
`key_id` (impressão digital) é público. Rotação gera uma nova chave e recifra
todos os segredos — quem roda a rotação é o `SecretVault` em conjunto.
"""

from __future__ import annotations

import base64
import contextlib
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..core.errors import KeyStoreError
from ..core.timeutil import iso
from .crypto import KEY_SIZE, key_id, new_master_key

ENV_VAR = "EGR_MASTER_KEY"
KEY_FILE_NAME = "master.key"
SECURE_MODE = 0o600


@dataclass(frozen=True)
class MasterKey:
    key_id: str
    raw: bytes
    source: str  # env | file


class MasterKeyStore:
    """Lê, cria e rotaciona a chave mestra de um workspace."""

    ENV_VAR = ENV_VAR

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)

    # ---- paths -------------------------------------------------------
    @property
    def key_path(self) -> Path:
        return self.workspace / ".egr" / KEY_FILE_NAME

    # ---- leitura -----------------------------------------------------
    def load(self) -> MasterKey | None:
        """Chave do ambiente tem precedência (permite cofre sem arquivo)."""

        from_env = os.environ.get(ENV_VAR)
        if from_env:
            raw = self._decode(from_env, source="ambiente")
            return MasterKey(key_id=key_id(raw), raw=raw, source="env")
        if self.key_path.exists():
            raw = self._decode(self.key_path.read_text(encoding="utf-8").strip(), source="arquivo")
            return MasterKey(key_id=key_id(raw), raw=raw, source="file")
        return None

    def require(self) -> MasterKey:
        """Chave obrigatória para qualquer operação do cofre."""

        key = self.load()
        if key is None:
            raise KeyStoreError(
                "cofre sem chave mestra: rode `egr key init` "
                f"(ou defina {ENV_VAR} com {KEY_SIZE} bytes em base64)"
            )
        return key

    # ---- escrita -----------------------------------------------------
    def create(self, *, force: bool = False, source: str = "file") -> MasterKey:
        existing = self.load()
        if existing is not None and not force:
            return existing
        raw = new_master_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_text(base64.urlsafe_b64encode(raw).decode("ascii"), encoding="utf-8")
        self._harden()
        return MasterKey(key_id=key_id(raw), raw=raw, source=source)

    def rotate(self, *, force: bool = False) -> tuple[MasterKey | None, MasterKey]:
        """Gera uma nova chave e devolve (chave_antiga, chave_nova)."""

        previous = self.load()
        if previous is not None and previous.source == "env" and not force:
            raise KeyStoreError(
                f"a chave ativa vem de {ENV_VAR}: rotacione a variável de ambiente "
                "e rode `egr key rotate`, ou use --force para assumir uma chave em arquivo"
            )
        new_key = self.create(force=True)
        return previous, new_key

    # ---- inspeção ----------------------------------------------------
    def status(self) -> dict:
        key = self.load()
        path = self.key_path
        mode = None
        if path.exists():
            mode = oct(stat.S_IMODE(path.stat().st_mode))[-3:]
        created_at = None
        if path.exists():
            created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
        return {
            "present": key is not None,
            "source": key.source if key else None,
            "key_id": key.key_id if key else None,
            "path": str(path),
            "mode": mode,
            "secure": (key.source == "env") if key else False,
            "created_at": created_at,
            "updated_at": iso(),
            "env_var": ENV_VAR,
        }

    def permission_problem(self) -> str | None:
        """Arquivo legível por outros usuários é um problema real, não um aviso."""

        if not self.key_path.exists():
            return None
        mode = stat.S_IMODE(self.key_path.stat().st_mode)
        if mode & 0o077:
            return f"{self.key_path} com permissão {oct(mode)[-3:]} (esperado 600)"
        return None

    # ---- helpers -----------------------------------------------------
    def _harden(self) -> None:
        # plataformas sem chmod (Windows) simplesmente ignoram
        with contextlib.suppress(OSError):
            os.chmod(self.key_path, SECURE_MODE)

    @staticmethod
    def _decode(value: str, *, source: str) -> bytes:
        try:
            padding = "=" * (-len(value) % 4)
            raw = base64.urlsafe_b64decode(value + padding)
        except (ValueError, TypeError) as exc:
            raise KeyStoreError(f"chave mestra inválida ({source}): esperado base64 de {KEY_SIZE} bytes") from exc
        if len(raw) != KEY_SIZE:
            raise KeyStoreError(f"chave mestra inválida ({source}): {len(raw)} bytes, esperado {KEY_SIZE}")
        return raw


__all__ = ["ENV_VAR", "MasterKey", "MasterKeyStore"]
