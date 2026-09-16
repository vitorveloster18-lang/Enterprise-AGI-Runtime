"""Memory belongs to the enterprise: knowledge, operational, episodic, semantic."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import MemoryKind


class MemoryRecord(BaseModel):
    id: str
    namespace: str = "default"
    kind: MemoryKind = MemoryKind.OPERATIONAL
    content: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = "runtime"
    task_id: str | None = None
    agent_id: str | None = None
    metadata: dict = Field(default_factory=dict)
    score: float | None = None  # relevance, filled on search
    created_at: datetime = Field(default_factory=utcnow)


class MemoryQuery(BaseModel):
    query: str = ""
    namespaces: list[str] = Field(default_factory=list)
    kinds: list[MemoryKind] = Field(default_factory=list)
    limit: int = 5
