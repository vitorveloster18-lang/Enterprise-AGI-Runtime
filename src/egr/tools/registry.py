"""Tool registry + controlled execution."""

from __future__ import annotations

from ..core.errors import ToolError, ToolNotFound
from ..core.timeutil import utcnow
from ..domain.tool import ToolResult
from .protocol import Tool, ToolContext


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    # ---- management --------------------------------------------------
    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFound(f"unknown tool '{name}'") from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def specs(self):
        return [tool.spec for tool in self._tools.values()]

    def list(self) -> list[dict]:
        return [tool.describe() for tool in sorted(self._tools.values(), key=lambda t: t.spec.name)]

    def catalog(self, allowed: list[str] | None = None) -> str:
        """Compact catalog injected into the planner prompt."""
        lines = []
        for tool in sorted(self._tools.values(), key=lambda item: item.spec.name):
            if allowed and not self._allowed(tool.spec.name, allowed):
                continue
            params = ", ".join(tool.spec.parameters.keys()) or "-"
            lines.append(
                f"- {tool.spec.name}({params}) :: {tool.spec.description} [risk={tool.spec.risk}]"
            )
        return "\n".join(lines)

    @staticmethod
    def _allowed(name: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            if pattern == name or pattern == "*":
                return True
            if pattern.endswith(".*") and name.startswith(pattern[:-1]):
                return True
        return False

    # ---- execution ---------------------------------------------------
    def execute(self, name: str, args: dict, ctx: ToolContext, **kwargs) -> ToolResult:
        from ..domain.tool import ToolRequest

        request = ToolRequest(
            tool=name,
            args=args or {},
            task_id=ctx.task_id,
            agent_id=ctx.agent_id,
            environment=ctx.environment,
            **kwargs,
        )
        started = utcnow()
        try:
            tool = self.get(name)
            tool.validate(request.args)
            result = tool.execute(request, ctx)
        except ToolError as exc:
            result = ToolResult.failure(str(exc))
        except Exception as exc:
            result = ToolResult.failure(f"{type(exc).__name__}: {exc}")
        elapsed = int((utcnow() - started).total_seconds() * 1000)
        result.duration_ms = result.duration_ms or elapsed
        result.metadata.setdefault("tool", name)
        return result
