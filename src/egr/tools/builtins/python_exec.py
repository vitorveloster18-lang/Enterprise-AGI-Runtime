"""Sandboxed Python execution — agora com dois backends (Fase 3).

    mode: auto      -> contêiner quando disponível, senão subprocesso
    mode: container -> docker/podman (rede desligada, limites de recursos)
    mode: process   -> subprocesso com ambiente filtrado (isolamento fraco)
"""

from __future__ import annotations

from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext
from ..sandbox import SandboxRunner


class PythonExecuteTool(Tool):
    spec = ToolSpec(
        name="python.execute",
        description="Executa um script Python no sandbox (contêiner quando disponível)",
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

        sandbox_config = ctx.security.get("sandbox") or {}
        runner = SandboxRunner(
            _sandbox_config(sandbox_config),
            workspace=ctx.workspace,
            sandbox_dir=ctx.sandbox,
            artifacts_dir=ctx.artifacts,
            environment=str(ctx.environment),
            task_id=ctx.task_id,
            agent_id=ctx.agent_id,
        )

        timeout = int(request.args.get("timeout", sandbox_config.get("timeout", ctx.timeout or 30)))
        result = runner.run(request.args["script"], timeout=timeout)

        payload = {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "artifacts": result.artifacts,
            "sandbox": {
                "mode": result.mode,
                "image": result.image,
                "network": result.network,
            },
        }
        metadata = {
            "sandbox_mode": result.mode,
            "sandbox_image": result.image,
            "sandbox_network": result.network,
            **result.metadata,
        }

        if not result.ok and result.error:
            return ToolResult.failure(result.error, output=payload, metadata=metadata)
        if result.exit_code != 0:
            return ToolResult.failure(
                f"script exited with code {result.exit_code}: {result.stderr[-500:]}",
                output=payload,
                metadata=metadata,
            )
        return ToolResult.success(payload, artifacts=result.artifacts, metadata=metadata)


def _sandbox_config(raw: dict):
    """Aceita tanto o dict do config quanto um SandboxConfig já construído."""

    from ...core.config import SandboxConfig

    if isinstance(raw, SandboxConfig):
        return raw
    if isinstance(raw, dict) and raw:
        known = {key: value for key, value in raw.items() if key in SandboxConfig.model_fields}
        return SandboxConfig(**known)
    return SandboxConfig()
