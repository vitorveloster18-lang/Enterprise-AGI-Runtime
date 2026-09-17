"""Integrações: sistema externo é fronteira, não extensão do agente.

Um conector é **declarado** (`integrations/*.yaml`), a credencial vive no cofre
(nunca no YAML) e cada chamada passa pelo Policy Engine antes de sair. O agente
não inventa endpoints: ele chama um conector que alguém declarou, com host,
métodos e leitura/escrita previamente autorizados.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import IntegrationEventStatus, IntegrationKind, JobStatus

READ_METHODS = ("GET", "HEAD", "OPTIONS")
#: operações normalizadas que não mudam nada em sistema alheio
READ_OPERATIONS = ("GET", "HEAD", "OPTIONS", "QUERY", "SELECT", "WITH", "PRAGMA", "EXPLAIN")


def quando(value: Any) -> str:
    """Data que sobrevive ao banco: datetime aqui, texto depois de recarregado."""

    if value is None:
        return "-"
    formatter = getattr(value, "isoformat", None)
    return str(formatter() if formatter else value)


class AuthConfig(BaseModel):
    """Como autenticar. O valor nunca aparece aqui — só a referência."""

    scheme: str = "none"          # none | bearer | header | basic
    secret: str = ""              # `vault:NOME` ou `VARIAVEL_DE_AMBIENTE`
    header: str = "Authorization"
    prefix: str = "Bearer"        # usado quando scheme == header


class InboundConfig(BaseModel):
    """Webhook de entrada: o sistema externo chama o Runtime."""

    enabled: bool = False
    #: referência do segredo compartilhado (HMAC) — `vault:NOME` ou env
    secret: str = ""
    #: cabeçalho que traz a assinatura
    signature_header: str = "X-EGR-Signature"
    #: cabeçalho (ou campo) com o id do evento, base da idempotência
    event_id_header: str = "X-EGR-Event-Id"
    #: tipo do evento publicado na trilha (gatilho de workflow)
    event_type: str = ""
    #: tolerância em segundos (replay não passa)
    tolerance_seconds: int = 300


class Integration(BaseModel):
    """Um sistema externo com permissão declarada de ser chamado."""

    id: str
    name: str = ""
    type: IntegrationKind = IntegrationKind.REST
    description: str = ""
    enabled: bool = False
    #: REST/GraphQL: base da URL · SQL: caminho do sqlite (ou DSN declarado)
    base_url: str = ""
    dsn: str = ""
    auth: AuthConfig = Field(default_factory=AuthConfig)
    #: somente estes hosts saem daqui (vazio = nenhum: nada sai)
    allowed_hosts: list[str] = Field(default_factory=list)
    #: somente estes métodos (vazio = só leitura)
    allowed_methods: list[str] = Field(default_factory=lambda: list(READ_METHODS))
    #: conector de leitura: escrita é recusada antes da política
    read_only: bool = True
    timeout: int = 20
    max_response_chars: int = 4000
    cost_per_call: float = 0.0
    headers: dict[str, str] = Field(default_factory=dict)
    #: cruza a fronteira da empresa (Data Boundary)
    external: bool = True
    inbound: InboundConfig = Field(default_factory=InboundConfig)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime | None = Field(default_factory=utcnow)
    updated_at: datetime | None = Field(default_factory=utcnow)

    @property
    def kind(self) -> str:
        return str(self.type)

    @property
    def target(self) -> str:
        return self.base_url or self.dsn or "-"

    def allows_method(self, method: str) -> bool:
        allowed = {item.upper() for item in self.allowed_methods if item}
        return method.upper() in allowed

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nome": self.name or self.id,
            "tipo": self.kind,
            "habilitado": self.enabled,
            "destino": self.target,
            "hosts": ", ".join(self.allowed_hosts) or "-",
            "métodos": ", ".join(self.allowed_methods) or "-",
            "somente_leitura": self.read_only,
            "autenticação": self.auth.scheme if self.auth.secret else "none",
            "custo_chamada": self.cost_per_call,
            "entrada": bool(self.inbound.enabled),
            "descrição": self.description or "-",
        }


class IntegrationCall(BaseModel):
    """Uma chamada medida: o que saiu, quanto custou e quem autorizou."""

    id: str
    integration: str
    kind: str = ""
    method: str = ""
    target: str = ""
    ok: bool | None = None
    status: int | None = None
    latency_ms: int = 0
    cost: float = 0.0
    decision: str = ""          # regra da política que autorizou (ou negou)
    approved: bool | None = None
    error: str = ""
    request_summary: str = ""
    response_summary: str = ""
    actor: str = "cli"
    task_id: str | None = None
    created_at: datetime | None = Field(default_factory=utcnow)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conector": self.integration,
            "tipo": self.kind,
            "método": self.method or "-",
            "destino": self.target or "-",
            "ok": self.ok,
            "status": self.status,
            "latência_ms": self.latency_ms,
            "custo": self.cost,
            "decisão": self.decision or "-",
            "erro": self.error or "-",
            "resposta": self.response_summary or "-",
            "ator": self.actor,
            "task": self.task_id or "-",
            "quando": quando(self.created_at),
        }


class InboundEvent(BaseModel):
    """Um evento que chegou de um sistema externo (idempotente por id)."""

    id: str
    integration: str
    external_id: str | None = None
    event_type: str = ""
    status: IntegrationEventStatus = IntegrationEventStatus.RECEIVED
    signature_ok: bool = False
    payload_summary: str = ""
    workflow_run: str | None = None
    error: str = ""
    created_at: datetime | None = Field(default_factory=utcnow)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conector": self.integration,
            "id externo": self.external_id or "-",
            "evento": self.event_type or "-",
            "status": str(self.status),
            "assinatura_ok": self.signature_ok,
            "resumo": self.payload_summary or "-",
            "workflow": self.workflow_run or "-",
            "erro": self.error or "-",
            "quando": quando(self.created_at),
        }


class IntegrationJob(BaseModel):
    """Uma chamada que o Runtime prometeu fazer — e vai tentar de novo."""

    id: str
    integration: str
    method: str = "GET"
    path: str = ""
    query: str = ""
    body: Any = None
    variables: dict = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    attempts: int = 0
    max_attempts: int = 3
    next_attempt: datetime | None = None
    last_error: str = ""
    call_id: str | None = None
    idempotency: str | None = None
    actor: str = "cli"
    created_at: datetime | None = Field(default_factory=utcnow)
    updated_at: datetime | None = Field(default_factory=utcnow)

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.max_attempts

    def wait_seconds(self, *, base: int = 30, cap: int = 3600) -> int:
        """Espera crescente: 1ª falha espera mais que nenhuma, e há teto."""

        return min(base * (2 ** max(0, self.attempts - 1)), cap)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conector": self.integration,
            "método": self.method,
            "destino": self.path or self.query or "-",
            "status": str(self.status),
            "tentativas": f"{self.attempts}/{self.max_attempts}",
            "próxima": self.next_attempt.isoformat() if self.next_attempt else "-",
            "chamada": self.call_id or "-",
            "idempotência": self.idempotency or "-",
            "erro": self.last_error or "-",
            "quando": quando(self.created_at),
        }


__all__ = [
    "READ_METHODS",
    "AuthConfig",
    "InboundConfig",
    "InboundEvent",
    "Integration",
    "IntegrationCall",
    "IntegrationJob",
]

