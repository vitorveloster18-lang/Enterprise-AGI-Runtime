"""Gateway de canais: a mensagem de fora vira trabalho governado por dentro.

Telegram, Slack e Web são **interfaces**. Nenhuma delas ganha um atalho: a
mensagem entra como `InboundMessage`, o Gateway resolve **quem** está falando
(`ChannelBinding` → `Principal`), confere permissão e entrega o objetivo ao
Runtime — que continua decidindo política, aprovação, custo e memória.

O que este módulo guarda é o mínimo para a conversa ser auditável: o pareamento,
os papéis e o histórico (já redigido).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import BindingStatus


class InboundMessage(BaseModel):
    """Uma mensagem que chegou de um canal — normalizada antes de governar."""

    channel: str
    external_id: str          # chat_id (Telegram), user_id (Slack), sessão (Web)
    text: str
    display_name: str = ""
    reply_to: str = ""        # para onde responder (Slack responde no canal)
    metadata: dict = Field(default_factory=dict)

    @property
    def binding_id(self) -> str:
        return f"{self.channel}:{self.external_id}"


class ChannelBinding(BaseModel):
    """O vínculo entre um remetente externo e um Principal do Runtime."""

    id: str                    # <channel>:<external_id>
    channel: str
    external_id: str
    display_name: str = ""
    principal_id: str = ""
    status: BindingStatus = BindingStatus.PENDING
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
    pairing_code: str = ""
    paired_by: str | None = None
    note: str = ""
    message_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime | None = None

    @staticmethod
    def new_code() -> str:
        return secrets.token_hex(3).upper()

    @property
    def active(self) -> bool:
        return self.status == BindingStatus.ACTIVE

    def touch(self) -> None:
        self.last_seen_at = utcnow()
        self.message_count += 1
        self.updated_at = utcnow()

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canal": self.channel,
            "remetente": self.external_id,
            "nome": self.display_name or "-",
            "principal": self.principal_id or "-",
            "status": str(self.status),
            "papéis": sorted(self.roles),
            "código": self.pairing_code or "-",
            "pareado_por": self.paired_by or "-",
            "mensagens": self.message_count,
            "visto_em": self.last_seen_at.isoformat() if self.last_seen_at else "-",
        }


class GatewayMessage(BaseModel):
    """Log de uma mensagem (entrada ou saída). Texto já redigido e truncado."""

    id: str
    channel: str
    direction: str                 # in | out
    external_id: str = ""
    text: str = ""
    task_id: str | None = None
    denied: bool = False
    reason: str = ""
    created_at: datetime = Field(default_factory=utcnow)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canal": self.channel,
            "direção": "entrada" if self.direction == "in" else "saída",
            "remetente": self.external_id or "-",
            "texto": self.text,
            "task": self.task_id or "-",
            "recusada": self.denied,
            "motivo": self.reason or "-",
            "quando": self.created_at.isoformat(),
        }


class GatewayReply(BaseModel):
    """O que o Gateway devolve ao canal (e ao chamador da API)."""

    text: str
    channel: str = ""
    external_id: str = ""
    task_id: str | None = None
    denied: bool = False
    reason: str = ""
    command: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "texto": self.text,
            "canal": self.channel,
            "remetente": self.external_id,
            "task": self.task_id or "-",
            "recusada": self.denied,
            "motivo": self.reason or "-",
            "comando": self.command or "-",
        }


__all__ = ["ChannelBinding", "GatewayMessage", "GatewayReply", "InboundMessage"]
