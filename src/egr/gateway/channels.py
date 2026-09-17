"""Canais: o pedaço de código que sabe falar com Telegram, Slack, Web ou stdin.

Um canal traduz mensagens — **nunca** governa. Ele entrega um `InboundMessage`
ao Gateway e devolve o `GatewayReply` pronto. Toda decisão (quem é você, o que
pode fazer, quanto custa, o que fica gravado) continua no Runtime.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from ..domain.channel import GatewayReply, InboundMessage

Handler = Callable[[InboundMessage], GatewayReply]


class BaseChannel:
    """Contrato mínimo: receber (opcional) e enviar."""

    kind = "base"

    def __init__(self, name: str, config: Any = None):
        self.name = name
        self.config = config

    # ---- saída -------------------------------------------------------
    def send(self, external_id: str, text: str, *, reply_to: str = "") -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    # ---- entrada ------------------------------------------------------
    def poll_once(self, handler: Handler) -> int:
        """Busca uma rodada de mensagens. Devolve quantas foram tratadas."""

        return 0

    def describe(self) -> dict[str, Any]:
        return {"nome": self.name, "tipo": self.kind}


class WebChannel(BaseChannel):
    """Chat do console web: a API entrega a mensagem e lê a caixa de saída."""

    kind = "web"

    def __init__(self, name: str = "web", config: Any = None):
        super().__init__(name, config)
        self.outbox: list[dict[str, Any]] = []

    def send(self, external_id: str, text: str, *, reply_to: str = "") -> dict[str, Any]:
        entry = {"canal": self.name, "remetente": external_id, "texto": text}
        self.outbox.append(entry)
        return entry

    def drain(self) -> list[dict[str, Any]]:
        pending, self.outbox = self.outbox, []
        return pending


class ConsoleChannel(BaseChannel):
    """Terminal: serve para demo, teste manual e para quem não tem bot."""

    kind = "console"

    def __init__(self, name: str = "console", config: Any = None, *, input_func=input, output_func=print):
        super().__init__(name, config)
        self._input = input_func
        self._output = output_func

    def send(self, external_id: str, text: str, *, reply_to: str = "") -> None:
        self._output(f"egr> {text}")

    def poll_once(self, handler: Handler) -> int:
        try:
            line = self._input("você> ")
        except (EOFError, KeyboardInterrupt):
            return -1
        if not line.strip():
            return 0
        reply = handler(
            InboundMessage(channel=self.name, external_id=external_id_for(self.name), text=line)
        )
        self.send(reply.external_id, reply.text)
        return 1

    def run(self, handler: Handler, *, max_turns: int = 0) -> int:
        turns = 0
        while True:
            handled = self.poll_once(handler)
            if handled < 0:
                return turns
            turns += handled
            if max_turns and turns >= max_turns:
                return turns


def external_id_for(channel: str) -> str:
    """Identidade estável do operador local (não confundir com Principal)."""

    return f"{channel}:{os.environ.get('USER', 'operador')}"


__all__ = ["BaseChannel", "ConsoleChannel", "Handler", "WebChannel", "external_id_for"]
