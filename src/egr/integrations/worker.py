"""Lacuna 12b: o dreno da fila em background — explícito, não escondido.

`egr integration drain` atende uma rodada e termina (feito para cron). O que
faltava era o **processo**: alguém que ficasse olhando a fila e chamasse o
dreno quando a espera vencesse.

Ele não é um daemon invisível dentro do Runtime: é um laço explícito, com
intervalo declarado, parada limpa por sinal e relatório do que fez. Quem opera
escolhe onde ele roda (terminal, systemd, container) — e a fila continua sendo
a mesma, com as mesmas regras:

    só pega job com janela vencida · cada tentativa passa pela política
    · falha aumenta a espera · esgotamento vira `failed` com motivo
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..core.timeutil import utcnow

DEFAULT_INTERVAL = 30.0
MAX_IDLE_INTERVAL = 300.0


class QueueWorker:
    """Fica de olho na fila e processa o que já pode ser processado."""

    def __init__(
        self,
        runtime: Any,
        *,
        interval: float = DEFAULT_INTERVAL,
        batch: int | None = None,
        idle_backoff: bool = True,
        max_interval: float = MAX_IDLE_INTERVAL,
        sleep: Callable[[float], None] = time.sleep,
        logger: Callable[[str], None] | None = None,
    ):
        self.runtime = runtime
        self.interval = max(0.5, float(interval))
        self.batch = batch
        self.idle_backoff = idle_backoff
        self.max_interval = max(self.interval, float(max_interval))
        self._sleep = sleep
        self._log = logger or (lambda message: None)
        self.rounds = 0
        self.processed = 0
        self.history: list[dict[str, Any]] = []
        self.stopped = False

    # ---- uma rodada --------------------------------------------------
    def round(self) -> dict[str, Any]:
        """Drena uma vez e conta o que aconteceu (nunca propaga exceção)."""

        started = utcnow()
        if not self.runtime.settings.config.integrations.queue.enabled:
            self.stopped = True
            report = {
                "quando": started.isoformat(),
                "processados": 0,
                "por_status": {},
                "erro": "fila desabilitada na configuração",
            }
            self.history.append(report)
            self._log("fila desabilitada: nada a drenar")
            return report

        try:
            rows = self.runtime.connectors.drain(limit=self.batch) if self.batch else self.runtime.connectors.drain()
        except Exception as exc:  # um provedor fora do ar não derruba o laço
            report = {
                "quando": started.isoformat(),
                "processados": 0,
                "por_status": {},
                "erro": str(exc),
            }
            self.history.append(report)
            self._log(f"falha no dreno: {exc}")
            return report

        counters: dict[str, int] = {}
        for row in rows:
            # o dreno devolve o resumo de cada job (dict)
            status = str(row.get("status") if isinstance(row, dict) else getattr(row, "status", "?"))
            counters[status] = counters.get(status, 0) + 1
        report = {
            "quando": started.isoformat(),
            "processados": len(rows),
            "por_status": counters,
            "erro": "",
        }
        self.history.append(report)
        self.rounds += 1
        self.processed += len(rows)
        if rows:
            self._log(f"{len(rows)} job(s): " + ", ".join(f"{key}={value}" for key, value in sorted(counters.items())))
        return report

    # ---- laço --------------------------------------------------------
    def run(self, *, max_rounds: int = 0, stop: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Roda até `stop()` (ou Ctrl+C). `max_rounds=0` é sem fim."""

        idle = self.interval
        while not self.stopped:
            report = self.round()
            if report.get("erro") in ("fila desabilitada na configuração",):
                break
            if stop is not None and stop():
                break
            rounds_done = self.rounds
            if max_rounds and rounds_done >= max_rounds:
                break
            busy = report["processados"] or not self.idle_backoff
            if busy:
                idle = self.interval
            self._sleep(idle)
            if not busy:
                # fila parada não merece martelar o banco: a espera cresce, com teto
                idle = min(idle * 2, self.max_interval)
        return self.summary()

    def stop(self) -> None:
        self.stopped = True

    def summary(self) -> dict[str, Any]:
        return {
            "rodadas": self.rounds,
            "processados": self.processed,
            "intervalo": self.interval,
            "parado": self.stopped,
            "últimas": self.history[-5:],
        }


__all__ = ["DEFAULT_INTERVAL", "MAX_IDLE_INTERVAL", "QueueWorker"]
