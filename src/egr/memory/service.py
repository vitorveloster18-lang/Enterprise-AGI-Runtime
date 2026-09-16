"""Memory service: knowledge / operational / episodic / semantic.

Memory belongs to the enterprise, is namespace-isolated and is always recorded
in the audit trail. V1 uses full-text search (SQLite FTS5); semantic/embedding
recall arrives with the Memory phase.
"""

from __future__ import annotations

from typing import Any

from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.enums import EventType, MemoryKind
from ..domain.memory import MemoryRecord
from ..storage.repositories import MemoryRepository


class MemoryService:
    def __init__(self, repository: MemoryRepository, audit=None, default_namespace: str = "default"):
        self.repository = repository
        self.audit = audit
        self.default_namespace = default_namespace

    # ---- write -------------------------------------------------------
    def write(
        self,
        content: str,
        *,
        kind: MemoryKind | str = MemoryKind.OPERATIONAL,
        namespace: str | None = None,
        summary: str = "",
        tags: list[str] | None = None,
        source: str = "runtime",
        task_id: str | None = None,
        agent_id: str | None = None,
        metadata: dict | None = None,
        environment: str | None = None,
    ) -> MemoryRecord:
        record = MemoryRecord(
            id=new_id("memory"),
            namespace=namespace or self.default_namespace,
            kind=MemoryKind(kind) if isinstance(kind, str) else kind,
            content=content,
            summary=summary or content[:280],
            tags=tags or [],
            source=source,
            task_id=task_id,
            agent_id=agent_id,
            metadata=metadata or {},
            created_at=utcnow(),
        )
        self.repository.save(record)
        if self.audit:
            self.audit.record(
                EventType.MEMORY_WRITTEN,
                actor=agent_id or source,
                task_id=task_id,
                agent_id=agent_id,
                environment=environment or "development",
                payload={
                    "memory_id": record.id,
                    "namespace": record.namespace,
                    "kind": str(record.kind),
                    "summary": record.summary,
                    "chars": len(content),
                },
            )
        return record

    # knowledge / episodic shortcuts
    def remember_knowledge(self, content: str, namespace: str = "default", **kwargs) -> MemoryRecord:
        return self.write(content, kind=MemoryKind.KNOWLEDGE, namespace=namespace, **kwargs)

    def remember_episode(self, content: str, namespace: str = "default", **kwargs) -> MemoryRecord:
        return self.write(content, kind=MemoryKind.EPISODIC, namespace=namespace, **kwargs)

    # ---- read --------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        namespaces: list[str] | None = None,
        kinds: list[str] | None = None,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        return self.repository.search(query, namespaces=namespaces, kinds=kinds, limit=limit)

    def recall(
        self,
        query: str,
        *,
        namespaces: list[str] | None = None,
        limit: int = 5,
        agent_id: str | None = None,
        task_id: str | None = None,
        environment: str | None = None,
    ) -> tuple[list[MemoryRecord], str]:
        """Recall memory and return (records, rendered context for the prompt)."""
        records = self.search(query, namespaces=namespaces, limit=limit)
        if self.audit and records:
            self.audit.record(
                EventType.MEMORY_RECALLED,
                actor=agent_id or "runtime",
                task_id=task_id,
                agent_id=agent_id,
                environment=environment or "development",
                payload={"query": query, "hits": [record.id for record in records]},
            )
        return records, self.render(records)

    @staticmethod
    def render(records: list[MemoryRecord]) -> str:
        if not records:
            return "(no relevant memory)"
        lines = []
        for record in records:
            lines.append(f"- [{record.kind}/{record.namespace}] {record.summary or record.content[:200]}")
        return "\n".join(lines)

    def list(self, namespace: str | None = None, limit: int = 20) -> list[MemoryRecord]:
        return self.repository.list(namespace=namespace, limit=limit)

    def stats(self) -> dict[str, Any]:
        return {
            "total": self.repository.count(),
            "by_kind": self.repository.stats(),
        }
