"""Loop dos canais: o Runtime conversando com o mundo, em primeiro plano.

Nada de mágica: um `while` que pergunta ao canal se chegou algo, entrega ao
Gateway e dorme um pouco. Erro de rede não derruba o processo (backoff), e
`stop()` encerra o laço sem matar a thread.
"""

from __future__ import annotations

import time
from typing import Any

from .channels import BaseChannel, ConsoleChannel, WebChannel
from .slack import SlackChannel
from .telegram import TelegramChannel

POLLABLE = (TelegramChannel,)


def build_channels(runtime: Any) -> list[BaseChannel]:
    """Instancia os canais declarados em `egr.yaml` (sem rede na construção)."""

    config = runtime.settings.config.gateway
    built: list[BaseChannel] = []
    for declared in config.channels:
        kind = (declared.type or "console").lower()
        if kind == "console":
            built.append(ConsoleChannel(declared.name, declared))
        elif kind == "web":
            built.append(WebChannel(declared.name, declared))
        elif kind == "telegram":
            built.append(TelegramChannel(declared.name, declared))
        elif kind == "slack":
            built.append(SlackChannel(declared.name, declared))
    if not built:
        built.append(WebChannel("web", None))
    return built


class Runner:
    """Roda canais de polling até `stop()` (ou Ctrl+C)."""

    def __init__(self, runtime: Any, channels: list[BaseChannel], *, interval: float = 1.0):
        self.runtime = runtime
        self.channels = channels
        self.interval = interval
        self._stop = False
        self.handled = 0
        self.errors: list[dict[str, Any]] = []

    def handler(self):
        return self.runtime.channels.handle_inbound

    def stop(self) -> None:
        self._stop = True

    def poll_all(self) -> int:
        """Uma rodada em todos os canais (usado por `--once` e pelos testes)."""

        total = 0
        for channel in self.channels:
            try:
                total += max(0, channel.poll_once(self.handler())) or 0
            except Exception as exc:  # um canal com problema não derruba os outros
                self.errors.append({"canal": channel.name, "erro": str(exc)})
        self.handled += total
        return total

    def run(self, *, max_rounds: int = 0) -> int:
        rounds = 0
        backoff = self.interval
        while not self._stop:
            handled = self.poll_all()
            rounds += 1
            backoff = self.interval if handled else min(backoff * 2, 30.0)
            if max_rounds and rounds >= max_rounds:
                break
            if self._stop:
                break
            time.sleep(backoff)
        return self.handled


def run(runtime: Any, names: list[str] | None = None, *, once: bool = False, interval: float = 1.0) -> Runner:
    """Atende os canais pedidos (ou todos os registrados)."""

    channels = list(runtime.channels.select(names))
    runner = Runner(runtime, channels, interval=interval)
    if once:
        runner.poll_all()
    else:
        runner.run()
    return runner


__all__ = ["POLLABLE", "Runner", "build_channels", "run"]
