"""Ferramenta `integration.call`: o agente usa conectores declarados, não inventa URLs.

O agente não sabe a URL, o token nem o host. Ele pede `integration.call` com o
nome do conector — o resto (host permitido, método permitido, credencial no
cofre, política, registro) é do Runtime.
"""

from __future__ import annotations

from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext


class IntegrationCallTool(Tool):
    spec = ToolSpec(
        name="integration.call",
        description="Chama um conector declarado (REST/GraphQL/SQL) — política decide",
        parameters={
            "integration": {"type": "string", "required": True},
            "method": {"type": "string", "required": False},
            "path": {"type": "string", "required": False},
            "query": {"type": "string", "required": False},
            "body": {"type": "string", "required": False},
            "variables": {"type": "object", "required": False},
        },
        risk=RiskLevel.MEDIUM,
        side_effects=True,
        requires_network=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        runtime = ctx.runtime
        if runtime is None or getattr(runtime, "connectors", None) is None:
            return ToolResult.failure("integration runtime is not available")
        if not runtime.settings.config.integrations.enabled:
            return ToolResult.failure("integrations are disabled by configuration")

        integration_id = str(request.args.get("integration") or "")
        method = str(request.args.get("method") or "GET").upper()
        path = str(request.args.get("path") or "")
        query = str(request.args.get("query") or "")
        body = request.args.get("body")
        variables = request.args.get("variables") or {}

        if ctx.dry_run:
            return ToolResult.success(
                {"dry_run": True, "integration": integration_id, "method": method, "path": path, "query": query}
            )

        call = runtime.connectors.call(
            integration_id,
            method=method,
            path=path,
            query=query,
            body=body,
            variables=variables,
            actor=ctx.agent_id or "agent",
            task_id=ctx.task_id,
            environment=str(ctx.environment),
            preauthorized=True,  # quem chegou aqui passou pelo Policy Engine
        )

        payload = {
            "id_da_chamada": call.id,
            "conector": call.integration,
            "método": call.method,
            "destino": call.target,
            "ok": call.ok,
            "status": call.status,
            "latência_ms": call.latency_ms,
            "custo": call.cost,
            "decisão": call.decision,
            "erro": call.error or None,
            "resposta": call.response_summary or None,
        }
        if not call.ok:
            return ToolResult.failure(call.error or "chamada recusada", output=payload)
        return ToolResult.success(payload, metadata={"external": True, "custo": call.cost})


class IntegrationListTool(Tool):
    spec = ToolSpec(
        name="integration.list",
        description="Lista os conectores declarados (sem credencial, sem segredo)",
        parameters={},
        risk=RiskLevel.LOW,
        side_effects=False,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        runtime = ctx.runtime
        if runtime is None or getattr(runtime, "connectors", None) is None:
            return ToolResult.failure("integration runtime is not available")
        items = [item.summary() for item in runtime.connectors.list()]
        return ToolResult.success({"conectores": items, "total": len(items)})


__all__ = ["IntegrationCallTool", "IntegrationListTool"]
