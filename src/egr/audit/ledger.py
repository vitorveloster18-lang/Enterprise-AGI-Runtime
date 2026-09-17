"""Append-only, hash-chained audit ledger.

Every event carries `prev_hash` and `hash = sha256(prev_hash | canonical(payload))`,
so tampering with any historical record breaks the chain and is detectable with
`egr audit verify`.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from ..core.hashing import GENESIS, chain_hash
from ..core.ids import new_id
from ..core.timeutil import iso, parse
from ..domain.enums import Environment, EventType
from ..domain.event import Event
from ..storage.database import Database


class AuditLedger:
    """Trilha append-only com hash encadeado.

    `record` é atômico: ler o último hash e escrever o próximo precisam ser uma
    só operação, senão duas threads encadeiam a partir do mesmo anterior — e a
    cadeia quebra (a carga da lacuna 8b descobriu isso na prática).
    """

    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.RLock()

    # ---- write -------------------------------------------------------
    def record(
        self,
        type: EventType | str,
        *,
        actor: str = "runtime",
        task_id: str | None = None,
        agent_id: str | None = None,
        environment: Environment | str = Environment.DEVELOPMENT,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> Event:
        with self._lock:
            previous = self.db.scalar("SELECT hash FROM events ORDER BY seq DESC LIMIT 1") or GENESIS
            event = Event(
                id=event_id or new_id("event"),
                type=type,
                actor=actor,
                task_id=task_id,
                agent_id=agent_id,
                environment=environment,
                payload=payload or {},
                prev_hash=previous,
            )
            event.hash = chain_hash(previous, self._hash_payload(event))

            with self.db.transaction():
                self.db.execute(
                    "INSERT INTO events (id, type, actor, task_id, agent_id, environment, payload, "
                    "created_at, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.id,
                        str(event.type),
                        event.actor,
                        event.task_id,
                        event.agent_id,
                        str(event.environment),
                        json.dumps(event.payload, ensure_ascii=False, default=str),
                        iso(event.created_at),
                        event.prev_hash,
                        event.hash,
                    ),
                )
            row = self.db.query_one("SELECT seq FROM events WHERE id = ?", (event.id,))
            event.seq = row["seq"] if row else None
        return event

    @staticmethod
    def _hash_payload(event: Event) -> dict[str, Any]:
        return {
            "id": event.id,
            "type": str(event.type),
            "actor": event.actor,
            "task_id": event.task_id,
            "agent_id": event.agent_id,
            "environment": str(event.environment),
            "payload": event.payload,
            "created_at": iso(event.created_at),
        }

    # ---- read --------------------------------------------------------
    def list(
        self,
        task_id: str | None = None,
        type: str | None = None,
        limit: int = 50,
    ) -> list[Event]:
        clauses, params = [], []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if type:
            clauses.append("type = ?")
            params.append(type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM events {where} ORDER BY seq DESC LIMIT ?", (*params, limit)
        )
        return [self._row_to_event(row) for row in rows]

    def recent(self, limit: int = 20) -> list[Event]:
        return self.list(limit=limit)

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM events") or 0)

    def get(self, event_id: str) -> Event | None:
        row = self.db.query_one("SELECT * FROM events WHERE id = ?", (event_id,))
        return self._row_to_event(row) if row else None

    # ---- integrity ---------------------------------------------------
    def verify(self) -> dict:
        rows = self.db.query("SELECT * FROM events ORDER BY seq")
        total = len(rows)
        broken: list[dict] = []
        previous = GENESIS
        for row in rows:
            payload = {
                "id": row["id"],
                "type": row["type"],
                "actor": row["actor"],
                "task_id": row["task_id"],
                "agent_id": row["agent_id"],
                "environment": row["environment"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
            expected = chain_hash(previous, payload)
            if expected != row["hash"] or row["prev_hash"] != previous:
                broken.append({"seq": row["seq"], "id": row["id"], "expected": expected})
            previous = row["hash"] or GENESIS
        return {
            "valid": not broken,
            "events": total,
            "broken": broken,
            "head": previous,
        }

    @staticmethod
    def _row_to_event(row) -> Event:
        return Event(
            id=row["id"],
            seq=row["seq"],
            type=row["type"],
            actor=row["actor"],
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            environment=row["environment"],
            payload=json.loads(row["payload"]),
            created_at=parse(row["created_at"]) or None,
            prev_hash=row["prev_hash"],
            hash=row["hash"],
        )
