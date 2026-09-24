"""Artifact: versioned output produced by agents (reports, files, plans, code)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow
from .enums import ArtifactKind, Environment


class Artifact(BaseModel):
    id: str
    kind: ArtifactKind = ArtifactKind.FILE
    name: str
    version: str = "1.0.0"
    path: str | None = None
    content_type: str = "text/markdown"
    summary: str = ""
    task_id: str | None = None
    agent_id: str | None = None
    environment: Environment = Environment.DEVELOPMENT
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
