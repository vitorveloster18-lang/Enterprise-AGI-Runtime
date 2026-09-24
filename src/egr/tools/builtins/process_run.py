"""Process execution: always requires approval (see baseline policy)."""

from __future__ import annotations

import shlex
import subprocess

from ...core.paths import resolve_within
from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext


class ProcessRunTool(Tool):
    spec = ToolSpec(
        name="process.run",
        description="Executa um comando do sistema sem shell, dentro do workspace",
        parameters={
            "argv": {"type": "array", "required": True, "help": "lista de argumentos"},
            "cwd": {"type": "string", "required": False},
            "timeout": {"type": "integer", "required": False},
        },
        risk=RiskLevel.CRITICAL,
        side_effects=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        argv = request.args["argv"]
        if isinstance(argv, str):
            argv = shlex.split(argv)
        if not argv:
            return ToolResult.failure("argv must not be empty")
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True, "argv": argv})

        cwd = resolve_within(ctx.workspace, request.args.get("cwd") or ".")
        timeout = int(request.args.get("timeout", ctx.timeout or 30))
        try:
            completed = subprocess.run(
                [str(item) for item in argv],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except FileNotFoundError:
            return ToolResult.failure(f"command not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"command timed out after {timeout}s")

        if completed.returncode != 0:
            return ToolResult.failure(
                f"command exited with {completed.returncode}: {completed.stderr[-500:]}",
                output={"exit_code": completed.returncode, "stdout": completed.stdout[-2000:]},
            )
        return ToolResult.success(
            {
                "argv": argv,
                "exit_code": completed.returncode,
                "stdout": completed.stdout[-8000:],
                "stderr": completed.stderr[-4000:],
            }
        )
