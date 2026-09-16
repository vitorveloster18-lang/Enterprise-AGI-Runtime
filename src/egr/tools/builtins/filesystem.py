"""Filesystem tools: always confined to the workspace (or its sandbox)."""

from __future__ import annotations

from pathlib import Path

from ...core.paths import resolve_within
from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}


class FilesystemListTool(Tool):
    spec = ToolSpec(
        name="filesystem.list",
        description="Lista arquivos e diretorios dentro do workspace",
        parameters={
            "path": {"type": "string", "required": False, "help": "caminho relativo (default '.')"},
            "recursive": {"type": "boolean", "required": False},
            "limit": {"type": "integer", "required": False},
        },
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        target = resolve_within(ctx.workspace, request.args.get("path") or ".")
        recursive = bool(request.args.get("recursive", False))
        limit = int(request.args.get("limit", 200))

        if not target.exists():
            return ToolResult.failure(f"path not found: {target}")
        if target.is_file():
            return ToolResult.success(
                {"entries": [self._entry(target, ctx.workspace)], "count": 1, "path": str(target)}
            )

        iterator = target.rglob("*") if recursive else target.iterdir()
        entries = []
        for path in iterator:
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            entries.append(self._entry(path, ctx.workspace))
            if len(entries) >= limit:
                break
        return ToolResult.success(
            {"path": str(target.relative_to(ctx.workspace)), "entries": entries, "count": len(entries)}
        )

    @staticmethod
    def _entry(path: Path, root: Path) -> dict:
        try:
            relative = str(path.relative_to(root))
        except ValueError:
            relative = str(path)
        stat = path.stat()
        return {
            "path": relative,
            "type": "directory" if path.is_dir() else "file",
            "size": stat.st_size if path.is_file() else 0,
            "modified": int(stat.st_mtime),
        }


class FilesystemReadTool(Tool):
    spec = ToolSpec(
        name="filesystem.read",
        description="Le o conteudo de um arquivo de texto dentro do workspace",
        parameters={
            "path": {"type": "string", "required": True},
            "max_chars": {"type": "integer", "required": False},
        },
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        target = resolve_within(ctx.workspace, request.args["path"])
        if not target.exists():
            return ToolResult.failure(f"file not found: {request.args['path']}")
        if target.is_dir():
            return ToolResult.failure(f"'{request.args['path']}' is a directory")
        max_chars = int(request.args.get("max_chars", 20000))
        raw = target.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.failure("binary file: use python.execute para inspecionar bytes")
        truncated = len(content) > max_chars
        return ToolResult.success(
            {
                "path": str(target.relative_to(ctx.workspace)),
                "content": content[:max_chars],
                "bytes": len(raw),
                "truncated": truncated,
            }
        )


class FilesystemWriteTool(Tool):
    spec = ToolSpec(
        name="filesystem.write",
        description="Escreve um arquivo dentro das raizes permitidas (artifacts/sandbox)",
        parameters={
            "path": {"type": "string", "required": True},
            "content": {"type": "string", "required": True},
        },
        risk=RiskLevel.MEDIUM,
        side_effects=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        roots = [
            (ctx.workspace / root).resolve()
            for root in ctx.security.get("allowed_write_roots", ["artifacts", ".egr/sandbox"])
        ]
        raw_path = Path(request.args["path"])
        target = raw_path.resolve() if raw_path.is_absolute() else (ctx.artifacts / raw_path).resolve()

        if not any(root == target or root in target.parents for root in roots):
            allowed = ", ".join(str(root) for root in roots)
            return ToolResult.failure(f"write denied: '{raw_path}' is outside the allowed roots ({allowed})")

        target.parent.mkdir(parents=True, exist_ok=True)
        content = request.args.get("content", "")
        target.write_text(content, encoding="utf-8")
        try:
            relative = str(target.relative_to(ctx.workspace))
        except ValueError:
            relative = str(target)
        return ToolResult.success(
            {
                "path": relative,
                "absolute": str(target),
                "bytes": len(content.encode("utf-8")),
                "artifact": True,
            },
            artifacts=[
                {
                    "name": target.name,
                    "path": str(target),
                    "bytes": len(content.encode("utf-8")),
                    "content_type": "text/markdown" if target.suffix in {".md", ".txt"} else "application/octet-stream",
                }
            ],
        )
