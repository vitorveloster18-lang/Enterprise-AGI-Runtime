"""HTTP tool: the only outbound door, always policy-governed."""

from __future__ import annotations

from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext

MAX_BODY_CHARS = 5000


class HttpRequestTool(Tool):
    spec = ToolSpec(
        name="http.request",
        description="Executa uma requisicao HTTP (fronteira externa da empresa)",
        parameters={
            "url": {"type": "string", "required": True},
            "method": {"type": "string", "required": False},
            "headers": {"type": "object", "required": False},
            "body": {"type": "string", "required": False},
            "timeout": {"type": "integer", "required": False},
        },
        risk=RiskLevel.HIGH,
        side_effects=True,
        requires_network=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        if not ctx.security.get("allow_network_tools", True):
            return ToolResult.failure("network tools are disabled by configuration")
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True, "url": request.args.get("url")})

        import httpx

        url = request.args["url"]
        method = (request.args.get("method") or "GET").upper()
        timeout = int(request.args.get("timeout", min(ctx.timeout or 30, 30)))
        headers = request.args.get("headers") or {}
        body = request.args.get("body")

        try:
            response = httpx.request(
                method,
                url,
                headers=headers,
                content=body,
                timeout=timeout,
                follow_redirects=True,
            )
        except Exception as exc:
            return ToolResult.failure(f"http request failed: {type(exc).__name__}: {exc}")

        text = response.text[:MAX_BODY_CHARS]
        return ToolResult.success(
            {
                "status": response.status_code,
                "url": str(response.url),
                "method": method,
                "headers": {key: value for key, value in response.headers.items() if key.lower() in {"content-type"}},
                "body": text,
                "truncated": len(response.text) > MAX_BODY_CHARS,
            },
            metadata={"external": True},
        )
