"""Sandboxed Python execution.

Runs in a subprocess with a filtered environment, inside the workspace sandbox,
under a hard timeout. Files produced by the script are copied into `artifacts/`
and registered as Artifacts by the runtime.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext

SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")


def build_environment(ctx: ToolContext) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS)
    }
    env.update(
        {
            "HOME": str(ctx.sandbox),
            "PYTHONPATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "EGR_WORKSPACE": str(ctx.workspace),
            "EGR_SANDBOX": str(ctx.sandbox),
            "EGR_ARTIFACTS": str(ctx.artifacts),
            "EGR_ENVIRONMENT": str(ctx.environment),
            "EGR_TASK_ID": ctx.task_id or "",
            "EGR_AGENT_ID": ctx.agent_id or "",
        }
    )
    return env


class PythonExecuteTool(Tool):
    spec = ToolSpec(
        name="python.execute",
        description="Executa um script Python isolado no sandbox do workspace",
        parameters={
            "script": {"type": "string", "required": True},
            "timeout": {"type": "integer", "required": False},
        },
        risk=RiskLevel.HIGH,
        side_effects=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        if not ctx.security.get("python_exec_enabled", True):
            return ToolResult.failure("python execution is disabled by configuration")
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True, "script_chars": len(request.args.get("script", ""))})

        script = request.args["script"]
        timeout = int(request.args.get("timeout", ctx.timeout or 30))
        ctx.sandbox.mkdir(parents=True, exist_ok=True)
        ctx.artifacts.mkdir(parents=True, exist_ok=True)

        script_path = ctx.sandbox / f"_egr_script_{uuid.uuid4().hex[:8]}.py"
        script_path.write_text(script, encoding="utf-8")
        before = self._snapshot(ctx.sandbox)

        try:
            completed = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(ctx.sandbox),
                env=build_environment(ctx),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"script timed out after {timeout}s")
        finally:
            script_path.unlink(missing_ok=True)

        artifacts = self._collect_artifacts(ctx, before)
        payload = {
            "exit_code": code,
            "stdout": stdout[-8000:],
            "stderr": stderr[-4000:],
            "artifacts": artifacts,
            "cwd": str(ctx.sandbox),
        }
        if code != 0:
            return ToolResult.failure(f"script exited with code {code}: {stderr[-500:]}", output=payload)
        return ToolResult.success(payload, artifacts=artifacts)

    # ---- helpers -----------------------------------------------------
    @staticmethod
    def _snapshot(sandbox: Path) -> dict[Path, float]:
        return {path: path.stat().st_mtime for path in sandbox.rglob("*") if path.is_file()}

    @staticmethod
    def _collect_artifacts(ctx: ToolContext, before: dict[Path, float]) -> list[dict]:
        destination_dir = ctx.artifacts / (ctx.task_id or "adhoc")
        produced: list[dict] = []
        for path in sorted(ctx.sandbox.rglob("*")):
            if not path.is_file() or path.name.startswith("_egr_script_"):
                continue
            if path in before and path.stat().st_mtime <= before[path]:
                continue
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / path.name
            destination.write_bytes(path.read_bytes())
            produced.append(
                {
                    "name": path.name,
                    "path": str(destination.relative_to(ctx.workspace)),
                    "absolute": str(destination),
                    "bytes": destination.stat().st_size,
                    "content_type": (
                        "text/markdown" if destination.suffix in {".md", ".txt"} else "application/octet-stream"
                    ),
                }
            )
        return produced
