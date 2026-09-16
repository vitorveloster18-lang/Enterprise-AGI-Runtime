"""MCP (Model Context Protocol) — servidores externos entram como Tools.

Cada ferramenta exposta por um servidor MCP é registrada como `mcp.<server>.<tool>`
e passa pelo mesmo caminho de governança: Agent -> Policy -> Tool -> Audit.

Como são descobertas em runtime, **não herdam nenhuma permissão**: sem regra de
política, o default deny bloqueia (comporte-se accordingly ao escrever políticas).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from ..core.config import MCPServerConfig
from ..core.logging import get_logger
from ..domain.enums import RiskLevel
from ..domain.tool import ToolRequest, ToolResult, ToolSpec
from .protocol import Tool, ToolContext

LOGGER = get_logger("egr.mcp")

PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    pass


class MCPClient:
    """Cliente JSON-RPC sobre stdio (o transporte mais comum em MCP)."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process: subprocess.Popen | None = None
        self._id = 0

    # ---- lifecycle ----------------------------------------------------
    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        env = {**os.environ, **self.config.env}
        try:
            self.process = subprocess.Popen(
                [self.config.command, *self.config.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise MCPError(f"cannot start MCP server '{self.config.name}': {exc}") from exc
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "egr", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized", {})

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    # ---- protocol -----------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        response = self._request("tools/list", {})
        return response.get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> dict[str, Any]:
        return self._request("tools/call", {"name": name, "arguments": arguments or {}})

    # ---- transport ----------------------------------------------------
    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, payload: dict) -> None:
        if not self.process or self.process.stdin is None:
            raise MCPError(f"MCP server '{self.config.name}' is not running")
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict) -> dict[str, Any]:
        self.start()
        request_id = self._next_id()
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

        assert self.process is not None and self.process.stdout is not None
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise MCPError(f"MCP server '{self.config.name}' closed the connection")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise MCPError(f"{method} failed: {message['error']}")
                return message.get("result", {})


class MCPToolProxy(Tool):
    """Um Tool do EGR que delega para uma ferramenta de um servidor MCP."""

    def __init__(self, client: MCPClient, server: str, remote_tool: dict, risk: RiskLevel = RiskLevel.MEDIUM):
        self.client = client
        self.server = server
        self.remote_name = remote_tool.get("name", "unknown")
        description = remote_tool.get("description") or f"Ferramenta MCP '{self.remote_name}'"
        schema = remote_tool.get("inputSchema") or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
        parameters = {
            name: {"type": (meta or {}).get("type", "string"), "required": name in required}
            for name, meta in properties.items()
        }
        self.spec = ToolSpec(
            name=f"mcp.{server}.{self.remote_name}",
            description=description[:300],
            parameters=parameters,
            risk=risk,
            side_effects=True,
            requires_network=False,
            tags=["mcp", server],
        )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True, "tool": self.remote_name, "args": request.args})
        try:
            result = self.client.call_tool(self.remote_name, request.args)
        except MCPError as exc:
            return ToolResult.failure(str(exc))
        content = result.get("content", [])
        text = "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        )
        return ToolResult.success(
            {"server": self.server, "tool": self.remote_name, "text": text, "raw": result},
            metadata={"mcp_server": self.server, "external": True},
        )


def connect_mcp_servers(config) -> tuple[list[MCPToolProxy], list[dict]]:
    """Descobre as ferramentas MCP e devolve (proxies, falhas)."""

    proxies: list[MCPToolProxy] = []
    failures: list[dict] = []
    if not config or not getattr(config, "enabled", True):
        return proxies, failures

    for server in getattr(config, "servers", []):
        if not server.enabled:
            continue
        client = MCPClient(server)
        try:
            tools = client.list_tools()
            for remote in tools:
                proxies.append(MCPToolProxy(client, server.name, remote))
            LOGGER.info("mcp: %s expôs %d ferramenta(s)", server.name, len(tools))
        except Exception as exc:
            failures.append({"server": server.name, "error": str(exc)})
            LOGGER.warning("mcp: falha ao conectar em '%s': %s", server.name, exc)
            client.stop()
    return proxies, failures
