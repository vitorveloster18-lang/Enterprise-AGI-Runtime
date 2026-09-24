"""Email tools — SMTP (envio) e IMAP (leitura), via stdlib.

Envio é sempre governado por política (`require_approval`) e pode ter custo
configurado (`email.cost_per_send`), que entra no custo total da task.
"""

from __future__ import annotations

import imaplib
import os
import smtplib
from email.message import EmailMessage

from ...domain.enums import RiskLevel
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext


def _header(raw: str, field: str) -> str:
    """Extrai um campo simples do header RFC822."""

    prefix = f"{field}:"
    for line in raw.splitlines():
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


class EmailSendTool(Tool):
    spec = ToolSpec(
        name="email.send",
        description="Envia um e-mail via SMTP (efeito colateral: exige aprovação)",
        parameters={
            "to": {"type": "string", "required": True},
            "subject": {"type": "string", "required": True},
            "body": {"type": "string", "required": True},
            "cc": {"type": "string", "required": False},
        },
        risk=RiskLevel.HIGH,
        side_effects=True,
        requires_network=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        config = ctx.security.get("email") or {}
        if not config or not config.get("enabled"):
            return ToolResult.failure(
                "email tools are disabled: configure tools.email (smtp_host, username_env, password_env)"
            )
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True, "to": request.args.get("to")})

        host = config.get("smtp_host")
        if not host:
            return ToolResult.failure("tools.email.smtp_host não configurado")

        message = EmailMessage()
        message["From"] = config.get("from_address") or "egr@local"
        message["To"] = request.args["to"]
        if request.args.get("cc"):
            message["Cc"] = request.args["cc"]
        message["Subject"] = request.args["subject"]
        message.set_content(request.args.get("body", ""))

        recipients = [request.args["to"]] + ([request.args["cc"]] if request.args.get("cc") else [])
        username = os.environ.get(config.get("username_env") or "", None)
        password = os.environ.get(config.get("password_env") or "", None)

        try:
            with smtplib.SMTP(host, int(config.get("smtp_port", 587)), timeout=ctx.timeout or 30) as server:
                if config.get("use_tls", True):
                    server.starttls()
                if username and password:
                    server.login(username, password)
                server.send_message(message, from_addr=message["From"], to_addrs=recipients)
        except Exception as exc:
            return ToolResult.failure(f"smtp send failed: {type(exc).__name__}: {exc}")

        return ToolResult.success(
            {"to": request.args["to"], "subject": request.args["subject"], "host": host},
            cost=float(config.get("cost_per_send", 0.0) or 0.0),
            metadata={"external": True},
        )


class EmailReadTool(Tool):
    spec = ToolSpec(
        name="email.read",
        description="Lê os últimos e-mails via IMAP (somente leitura)",
        parameters={
            "folder": {"type": "string", "required": False},
            "limit": {"type": "integer", "required": False},
            "unread_only": {"type": "boolean", "required": False},
        },
        risk=RiskLevel.MEDIUM,
        requires_network=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        config = ctx.security.get("email") or {}
        if not config or not config.get("enabled"):
            return ToolResult.failure("email tools are disabled: configure tools.email.imap_host")
        host = config.get("imap_host")
        if not host:
            return ToolResult.failure("tools.email.imap_host não configurado")
        if ctx.dry_run:
            return ToolResult.success({"dry_run": True})

        username = os.environ.get(config.get("username_env") or "", None)
        password = os.environ.get(config.get("password_env") or "", None)
        limit = int(request.args.get("limit", 10))
        folder = request.args.get("folder", "INBOX")

        try:
            with imaplib.IMAP4_SSL(host, int(config.get("imap_port", 993))) as client:
                if username and password:
                    client.login(username, password)
                client.select(folder)
                criteria = "(UNSEEN)" if request.args.get("unread_only") else "ALL"
                _, data = client.search(None, criteria)
                ids = data[0].split()[-limit:]
                messages = []
                for message_id in ids:
                    _, payload = client.fetch(message_id, "(RFC822.HEADER)")
                    header = (
                        payload[0][1].decode("utf-8", errors="replace") if payload and payload[0] else ""
                    )
                    messages.append(
                        {
                            "id": message_id.decode(),
                            "from": _header(header, "from"),
                            "subject": _header(header, "subject"),
                        }
                    )
        except Exception as exc:
            return ToolResult.failure(f"imap read failed: {type(exc).__name__}: {exc}")

        return ToolResult.success({"folder": folder, "count": len(messages), "messages": messages})
