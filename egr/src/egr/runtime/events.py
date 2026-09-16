"""In-process event bus (Phase 6 orchestration will grow this into a real bus)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..domain.enums import Environment, EventType


class EventBus:
    def __init__(self, audit=None):
        self.audit = audit
        self._subscribers: list[Callable[[Any], None]] = []

    def subscribe(self, handler: Callable[[Any], None]) -> None:
        self._subscribers.append(handler)

    def publish(
        self,
        type: EventType | str,
        *,
        actor: str = "runtime",
        task_id: str | None = None,
        agent_id: str | None = None,
        environment: Environment | str = Environment.DEVELOPMENT,
        payload: dict | None = None,
    ):
        event = None
        if self.audit:
            event = self.audit.record(
                type,
                actor=actor,
                task_id=task_id,
                agent_id=agent_id,
                environment=environment,
                payload=payload,
            )
        for handler in self._subscribers:
            try:
                handler(event)
            except Exception:
                continue
        return event
