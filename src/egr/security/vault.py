"""Cofre de segredos: credenciais cifradas em repouso, nunca em `egr.yaml`.

    segredo em claro -> envelope cifrado (EGR1) -> SQLite
                             ^                       |
                             +---- chave mestra -----+

Regras que o Runtime impõe:

* o valor em claro **nunca** é persistido, logado ou auditado — só o envelope;
* `list()` devolve metadados; `reveal()` exige a chave mestra e é o único ponto
  em que o segredo volta à memória;
* cada segredo tem salt próprio (vazamento de um não enfraquece os outros);
* rotação = recifrar com nova chave (`reencrypt_all`) — nunca reescrever em
  claro.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.errors import VaultError
from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.enums import EventType
from . import crypto
from .keystore import MasterKey, MasterKeyStore

VAULT_PREFIX = "vault:"


class SecretRecord(BaseModel):
    """Metadados + envelope cifrado. O campo `ciphertext` **não** é o segredo."""

    id: str
    name: str
    provider: str | None = None
    description: str = ""
    environment: str = "all"
    key_id: str
    salt: str
    ciphertext: str
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    rotated_at: datetime | None = None
    created_by: str = "cli"
    metadata: dict = Field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        """Linha segura para CLI/API: sem envelope e sem plaintext."""

        return {
            "name": self.name,
            "provider": self.provider or "-",
            "description": self.description,
            "environment": self.environment,
            "key_id": self.key_id,
            "version": self.version,
            "bytes": len(self.ciphertext),
            "created_by": self.created_by,
            "updated_at": self.updated_at.isoformat(),
            "rotated_at": self.rotated_at.isoformat() if self.rotated_at else None,
        }


class SecretVault:
    """Cofre: o único lugar do Runtime que pode devolver um segredo em claro."""

    def __init__(self, repository, keystore: MasterKeyStore, audit=None, actor: str = "cli"):
        self.repository = repository
        self.keystore = keystore
        self.audit = audit
        self.actor = actor

    # ---- escrita ------------------------------------------------------
    def put(
        self,
        name: str,
        value: str,
        *,
        provider: str | None = None,
        description: str = "",
        environment: str = "all",
        actor: str | None = None,
        metadata: dict | None = None,
    ) -> SecretRecord:
        if not name:
            raise VaultError("nome do segredo é obrigatório")
        if not value:
            raise VaultError("valor do segredo é obrigatório (o cofre não guarda vazio)")
        key = self.keystore.require()
        envelope, salt = crypto.encrypt(value, key.raw)
        existing = self.repository.get(name)
        now = utcnow()
        record = SecretRecord(
            id=existing.id if existing else new_id("secret"),
            name=name,
            provider=provider,
            description=description or (existing.description if existing else ""),
            environment=environment,
            key_id=key.key_id,
            salt=salt.hex(),
            ciphertext=envelope,
            version=(existing.version + 1) if existing else 1,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            rotated_at=now if existing else None,
            created_by=(existing.created_by if existing else (actor or self.actor)),
            metadata=metadata or (existing.metadata if existing else {}),
        )
        self.repository.save(record)
        self._record(
            EventType.SECRET_ROTATED if existing else EventType.SECRET_STORED,
            actor or self.actor,
            {"secret": name, "provider": provider, "key_id": key.key_id, "version": record.version},
        )
        return record

    def rotate(self, name: str, value: str, *, actor: str | None = None) -> SecretRecord:
        if self.repository.get(name) is None:
            raise VaultError(f"segredo '{name}' não existe")
        return self.put(name, value, actor=actor)

    def delete(self, name: str, *, actor: str | None = None) -> bool:
        removed = self.repository.delete(name)
        if removed:
            self._record(EventType.SECRET_REMOVED, actor or self.actor, {"secret": name})
        return removed

    # ---- leitura ------------------------------------------------------
    def get(self, name: str) -> SecretRecord | None:
        return self.repository.get(name)

    def reveal(self, name: str) -> str | None:
        record = self.repository.get(name)
        if record is None:
            return None
        key = self.keystore.require()
        return crypto.decrypt_text(record.ciphertext, key.raw, bytes.fromhex(record.salt))

    def list(self) -> list[dict[str, Any]]:
        return [record.as_row() for record in self.repository.list_records()]

    def count(self) -> int:
        return self.repository.count()

    # ---- chaves -------------------------------------------------------
    def reencrypt_all(self, previous: MasterKey | None, current: MasterKey) -> int:
        """Recifra todos os segredos sob a nova chave (rotação da mestra)."""

        reencrypted = 0
        for record in self.repository.list_records():
            if previous is not None:
                value = crypto.decrypt_text(record.ciphertext, previous.raw, bytes.fromhex(record.salt))
            else:  # pragma: no cover - sem chave anterior não há o que migrar
                continue
            envelope, salt = crypto.encrypt(value, current.raw)
            record.ciphertext = envelope
            record.salt = salt.hex()
            record.key_id = current.key_id
            record.updated_at = utcnow()
            record.rotated_at = utcnow()
            self.repository.save(record)
            reencrypted += 1
        return reencrypted

    # ---- resolução ----------------------------------------------------
    def resolve_reference(self, reference: str | None) -> str | None:
        """Resolve referências `vault:NOME`. Outras refs não são do cofre."""

        if not reference:
            return None
        if not reference.startswith(VAULT_PREFIX):
            return None
        name = reference[len(VAULT_PREFIX) :].strip()
        if not name:
            raise VaultError(f"referência de cofre inválida: '{reference}'")
        return self.reveal(name)

    def status(self) -> dict:
        key_status = self.keystore.status()
        return {
            "envelope": crypto.ENVELOPE_VERSION,
            "master_key": key_status,
            "secrets": self.count(),
            "permission_problem": self.keystore.permission_problem(),
        }

    # ---- interno ------------------------------------------------------
    def _record(self, event_type: EventType | str, actor: str, payload: dict) -> None:
        if self.audit is None:
            return
        self.audit.record(event_type, actor=actor, payload=payload)


__all__ = ["VAULT_PREFIX", "SecretRecord", "SecretVault"]
