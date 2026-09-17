"""Tool protocol: the boundary between the Agent and the world.

    Agent -> Tool Request -> Policy -> Tool Executor -> External System
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.enums import Environment, RiskLevel
from ..domain.tool import ToolRequest, ToolResult, ToolSpec


@dataclass
class ToolContext:
    """Everything a tool is allowed to see / touch."""

    workspace: Path
    sandbox: Path
    artifacts: Path
    environment: Environment = Environment.DEVELOPMENT
    enterprise_id: str = "local"
    task_id: str | None = None
    agent_id: str | None = None
    timeout: int = 30
    dry_run: bool = False
    security: dict = field(default_factory=dict)
    database_path: Path | None = None
    # Ferramentas de plataforma (dev.*) precisam falar com o Runtime — que continua
    # no controle: elas são autorizadas pela política como qualquer outra.
    runtime: Any | None = None


class Tool(ABC):
    spec: ToolSpec

    def __init__(self, spec: ToolSpec | None = None):
        if spec is not None:
            self.spec = spec

    # ---- contract ----------------------------------------------------
    @abstractmethod
    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult: ...

    # ---- helpers -----------------------------------------------------
    def validate(self, args: dict) -> None:
        required = [
            name
            for name, schema in (self.spec.parameters or {}).items()
            if isinstance(schema, dict) and schema.get("required")
        ]
        missing = [name for name in required if name not in args or args[name] in (None, "")]
        if missing:
            raise ValueError(f"missing required parameter(s): {', '.join(missing)}")

    @property
    def risk(self) -> RiskLevel:
        return self.spec.risk

    def describe(self) -> dict:
        return {
            "name": self.spec.name,
            "description": self.spec.description,
            "risk": str(self.spec.risk),
            "side_effects": self.spec.side_effects,
            "requires_network": self.spec.requires_network,
            "parameters": self.spec.parameters,
        }

    def timed(self, func, *args, **kwargs) -> tuple[object, int]:
        started = time.perf_counter()
        result = func(*args, **kwargs)
        return result, int((time.perf_counter() - started) * 1000)
