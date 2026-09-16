"""Scheduler: quem dispara no tempo é o operador, não um daemon escondido.

`egr workflow tick` é idempotente e termina — feito para ser chamado por cron do
SO, systemd timer ou um job do Kubernetes. `egr workflow schedule` é só um laço
que chama o tick a cada intervalo, para desenvolvimento.

Idempotência: se já existe run do mesmo workflow com o mesmo trigger no mesmo
minuto, o disparo é ignorado (sem duplicar execução por tick sobreposto).
"""

from __future__ import annotations

from datetime import datetime

from ..core.timeutil import utcnow
from ..orchestration.cron import CronError, CronExpression


class WorkflowScheduler:
    def __init__(self, runtime):
        self.runtime = runtime

    # ---- consulta -----------------------------------------------------
    def due(self, now: datetime | None = None) -> list[dict]:
        """Workflows com cron vencido neste minuto (e não executados ainda)."""

        moment = now or utcnow()
        self.runtime._load_workflows()
        scheduled: list[dict] = []
        for workflow in self.runtime.workflows.values():
            trigger = workflow.trigger
            if trigger.type != "cron" or not trigger.enabled or not trigger.cron:
                continue
            try:
                expression = CronExpression(trigger.cron)
            except CronError as error:
                scheduled.append({"workflow": workflow.id, "error": str(error)})
                continue
            if not expression.matches(moment):
                continue
            last = self.runtime.runs.last_run(workflow.id, trigger="cron")
            if last and _same_minute(last.created_at, moment):
                continue
            scheduled.append(
                {
                    "workflow": workflow.id,
                    "cron": trigger.cron,
                    "environment": str(workflow.environment),
                    "due_at": moment.replace(second=0, microsecond=0).isoformat(),
                }
            )
        return scheduled

    def upcoming(self, limit: int = 5, now: datetime | None = None) -> list[dict]:
        """Próximos disparos previstos (apoio a `egr workflow schedule`)."""

        moment = now or utcnow()
        self.runtime._load_workflows()
        planned: list[dict] = []
        for workflow in self.runtime.workflows.values():
            trigger = workflow.trigger
            if trigger.type != "cron" or not trigger.enabled or not trigger.cron:
                continue
            try:
                expression = CronExpression(trigger.cron)
            except CronError as error:
                planned.append({"workflow": workflow.id, "error": str(error)})
                continue
            next_at = expression.next_after(moment)
            planned.append(
                {
                    "workflow": workflow.id,
                    "cron": trigger.cron,
                    "next_at": next_at.isoformat() if next_at else None,
                }
            )
        planned.sort(key=lambda item: item.get("next_at") or "")
        return planned[:limit]

    # ---- execução -----------------------------------------------------
    def tick(self, *, now: datetime | None = None, dry_run: bool = False, created_by: str = "scheduler") -> list:
        """Roda os agendamentos vencidos. Devolve a lista de runs iniciados."""

        moment = now or utcnow()
        started = []
        for item in self.due(moment):
            if "error" in item:
                continue
            if dry_run:
                started.append(item)
                continue
            run = self.runtime.orchestrator.start(
                item["workflow"],
                environment=item.get("environment"),
                created_by=created_by,
                trigger="cron",
                trigger_detail=item["cron"],
            )
            started.append(run)
        return started

    def loop(self, *, interval_seconds: int = 60, max_ticks: int | None = None, dry_run: bool = False):
        """Laço simples para desenvolvimento (use cron do SO em produção)."""

        import time

        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            yield self.tick(dry_run=dry_run)
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                time.sleep(max(1, interval_seconds))


def _same_minute(left: datetime, right: datetime) -> bool:
    """Compara só o minuto, ignorando fuso (o tick é local, o run é UTC)."""

    return left.replace(second=0, microsecond=0, tzinfo=None) == right.replace(
        second=0, microsecond=0, tzinfo=None
    )


__all__ = ["WorkflowScheduler"]
