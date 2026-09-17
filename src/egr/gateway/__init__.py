"""Gateway de canais (Fase 10): Remote Control sem abrir mão do governo.

Telegram, Slack, Web e terminal são **interfaces** sobre a mesma API local. Cada
mensagem entra por `GatewayService.handle_inbound()`, que:

    identifica (pareamento) → autoriza (RBAC + lista branca + ritmo)
    → executa (task governada por política, orçamento e aprovação)
    → responde (texto redigido) → registra (trilha auditada)

O canal nunca é um atalho: o que ele pode fazer é o que o Principal pareado
pode fazer.
"""

from .channels import BaseChannel, ConsoleChannel, WebChannel
from .loop import Runner, build_channels, run
from .service import GatewayService
from .slack import SlackChannel, verify_signature
from .telegram import TelegramChannel

__all__ = [
    "BaseChannel",
    "ConsoleChannel",
    "GatewayService",
    "Runner",
    "SlackChannel",
    "TelegramChannel",
    "WebChannel",
    "build_channels",
    "run",
    "verify_signature",
]
