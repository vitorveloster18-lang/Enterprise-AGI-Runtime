"""Gatilhos por evento: a auditoria também é uma fila.

Todo evento do ledger passa pelo EventBus; se ele casa com o `trigger.event` de
um workflow, o run começa. Nada de broker externo: o Runtime já tem a trilha de
eventos — o gatilho é uma leitura dela.

Proteção contra realimentação: eventos originados **dentro** de um run
(`payload.workflow_run`) são ignorados, senão "task.*" dispararia o workflow que
acabou de criar a task, para sempre.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import EventType


def matches(pattern: str | None, event_type: str) -> bool:
    """Casa tipo exato ou prefixo (`task.*`)."""

    if not pattern:
        return False
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return event_type.startswith(pattern[:-1])
    return pattern == event_type


def bind(runtime, *, environment: str | None = None) -> int:
    """Inscreve o handler de triggers no EventBus. Devolve qtd de workflows ligados."""

    if getattr(runtime, "_triggers_bound", False):
        return _bound_count(runtime)

    def handler(event: Any) -> None:
        if event is None:
            return
        event_type = str(getattr(event, "type", ""))
        payload = getattr(event, "payload", None) or {}
        if payload.get("workflow_run"):
            return  # evento interno de um run: não realimenta
        runtime._load_workflows()
        for workflow in runtime.workflows.values():
            trigger = workflow.trigger
            if trigger.type != "event" or not trigger.enabled:
                continue
            if not matches(trigger.event, event_type):
                continue
            try:
                runtime.orchestrator.start(
                    workflow.id,
                    inputs={"event": event_type, "event_id": getattr(event, "id", None), "payload": payload},
                    environment=environment or str(workflow.environment),
                    created_by="trigger",
                    trigger="event",
                    trigger_detail=f"{event_type}:{getattr(event, 'id', '')}",
                )
            except Exception as error:  # falha de trigger não derruba o Runtime
                runtime.audit.record(
                    EventType.SYSTEM_EVENT,
                    actor="runtime",
                    payload={
                        "action": "workflow_trigger_failed",
                        "workflow": workflow.id,
                        "event": event_type,
                        "error": str(error),
                    },
                )

    runtime.events.subscribe(handler)
    runtime._triggers_bound = True
    return _bound_count(runtime)


def _bound_count(runtime) -> int:
    runtime._load_workflows()
    return len(
        [
            workflow
            for workflow in runtime.workflows.values()
            if workflow.trigger.type == "event" and workflow.trigger.enabled
        ]
    )


def planned(runtime) -> list[dict]:
    """Quais eventos estão ligados a quais workflows (para `egr workflow triggers`)."""

    runtime._load_workflows()
    return [
        {
            "workflow": workflow.id,
            "evento": workflow.trigger.event or "*",
            "ambiente": str(workflow.environment),
            "ativo": workflow.trigger.enabled,
        }
        for workflow in runtime.workflows.values()
        if workflow.trigger.type == "event"
    ]


__all__ = ["bind", "matches", "planned"]
