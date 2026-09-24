"""Git tools — leitura livre, escrita governada.

Operações de leitura (status/diff/log/show) são de baixo risco.
`git.commit` tem efeito colateral e exige aprovação humana (política baseline).
"""

from __future__ import annotations

import subprocess

from ...core.paths import resolve_within
from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext

MAX_OUTPUT = 8000


class GitTool(Tool):
    """Base para ferramentas git dentro do workspace."""

    spec = ToolSpec(name="git.base", description="base")
    allow_write = False

    def _run(self, ctx: ToolContext, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
        binary = ctx.security.get("git_binary", "git")
        workdir = resolve_within(ctx.workspace, cwd or ".")
        return subprocess.run(
            [binary, *args],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=min(ctx.timeout or 30, 60),
            check=False,
        )

    @staticmethod
    def _completed(result: subprocess.CompletedProcess, command: str) -> ToolResult:
        output = result.stdout[-MAX_OUTPUT:]
        if result.returncode != 0:
            return ToolResult.failure(
                f"git {command} failed ({result.returncode}): {result.stderr[-500:]}",
                output={"command": command, "stdout": output},
            )
        return ToolResult.success({"command": command, "stdout": output, "stderr": result.stderr[-1000:]})


class GitStatusTool(GitTool):
    spec = ToolSpec(
        name="git.status",
        description="Mostra o estado do repositório (arquivos modificados, branch)",
        parameters={"cwd": {"type": "string", "required": False}},
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        if not ctx.security.get("git_enabled", True):
            return ToolResult.failure("git tools are disabled by configuration")
        result = self._run(ctx, ["status", "--short", "--branch"], request.args.get("cwd"))
        return self._completed(result, "status")


class GitDiffTool(GitTool):
    spec = ToolSpec(
        name="git.diff",
        description="Mostra o diff do repositório (ou de um caminho)",
        parameters={
            "cwd": {"type": "string", "required": False},
            "path": {"type": "string", "required": False},
            "staged": {"type": "boolean", "required": False},
        },
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        if not ctx.security.get("git_enabled", True):
            return ToolResult.failure("git tools are disabled by configuration")
        args = ["diff"]
        if request.args.get("staged"):
            args.append("--staged")
        if request.args.get("path"):
            args += ["--", request.args["path"]]
        result = self._run(ctx, args, request.args.get("cwd"))
        return self._completed(result, "diff")


class GitLogTool(GitTool):
    spec = ToolSpec(
        name="git.log",
        description="Mostra o histórico de commits",
        parameters={
            "cwd": {"type": "string", "required": False},
            "limit": {"type": "integer", "required": False},
        },
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        if not ctx.security.get("git_enabled", True):
            return ToolResult.failure("git tools are disabled by configuration")
        limit = int(request.args.get("limit", 10))
        result = self._run(
            ctx,
            ["log", f"--max-count={limit}", "--pretty=format:%h|%an|%ad|%s", "--date=short"],
            request.args.get("cwd"),
        )
        completed = self._completed(result, "log")
        if completed.ok:
            lines = []
            for line in completed.output["stdout"].splitlines():
                parts = line.split("|", 3)
                if len(parts) == 4:
                    lines.append({"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
            completed.output["commits"] = lines
        return completed


class GitCommitTool(GitTool):
    """Efeito colateral: política exige aprovação fora de desenvolvimento."""

    spec = ToolSpec(
        name="git.commit",
        description="Cria um commit no repositório (sempre governado por política)",
        parameters={
            "message": {"type": "string", "required": True},
            "cwd": {"type": "string", "required": False},
            "paths": {"type": "array", "required": False},
        },
        risk=RiskLevel.HIGH,
        side_effects=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        if not ctx.security.get("git_enabled", True):
            return ToolResult.failure("git tools are disabled by configuration")
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True, "message": request.args.get("message")})

        paths = request.args.get("paths") or ["."]
        add = self._run(ctx, ["add", *[str(item) for item in paths]], request.args.get("cwd"))
        if add.returncode != 0:
            return self._completed(add, "add")

        commit = self._run(
            ctx,
            ["commit", "-m", request.args["message"]],
            request.args.get("cwd"),
        )
        result = self._completed(commit, "commit")
        if result.ok:
            head = self._run(ctx, ["rev-parse", "HEAD"], request.args.get("cwd"))
            result.output["commit"] = head.stdout.strip()
        return result
