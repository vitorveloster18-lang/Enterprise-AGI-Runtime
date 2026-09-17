"""Canal Slack: Events API (assinatura verificada) + `chat.postMessage`.

O Slack manda um POST assinado com HMAC do corpo (`v0=<hex>`). Sem assinatura
válida (ou fora da janela de 5 minutos) o payload nem chega ao Gateway: replay
de mensagem é ataque, não conversa.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

from ..domain.channel import GatewayReply, InboundMessage
from .channels import BaseChannel, Handler

API = "https://slack.com/api"
TOLERANCE_SECONDS = 300


def signature_for(secret: str, timestamp: str, body: str) -> str:
    base = f"v0:{timestamp}:{body}".encode()
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, body: str, signature: str, *, now: float | None = None) -> bool:
    """Confere a assinatura do Slack — e recusa payloads antigos (replay)."""

    if not secret or not timestamp or not signature:
        return False
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - stamp) > TOLERANCE_SECONDS:
        return False
    expected = signature_for(secret, timestamp, body)
    return hmac.compare_digest(expected, signature or "")


class SlackChannel(BaseChannel):
    """Recebe eventos HTTP e responde no mesmo canal."""

    kind = "slack"

    def __init__(self, name: str, config: Any = None, *, client: Any = None, token: str | None = None):
        super().__init__(name, config)
        self._client = client
        self._token = token
        self._targets: dict[str, str] = {}  # usuário → canal onde responder

    @property
    def token(self) -> str:
        if self._token:
            return self._token
        env = self.config.bot_token_env if self.config else "EGR_SLACK_TOKEN"
        return os.environ.get(env, "")

    @property
    def signing_secret(self) -> str:
        env = self.config.signing_secret_env if self.config else "EGR_SLACK_SIGNING_SECRET"
        return os.environ.get(env, "")

    # ---- entrada ------------------------------------------------------
    def handle_payload(self, payload: dict, handler: Handler) -> GatewayReply | None:
        """Trata um evento do Slack. `url_verification` devolve o desafio."""

        if not isinstance(payload, dict):
            return None
        kind = payload.get("type")
        if kind == "url_verification":
            return GatewayReply(text=str(payload.get("challenge") or ""), channel=self.name, command="url_verification")

        event = payload.get("event") or {}
        if kind != "event_callback" or event.get("type") not in ("message", "app_mention"):
            return None
        if event.get("bot_id") or event.get("subtype"):
            return None  # mensagem do próprio bot: não responder a si mesmo

        user = str(event.get("user") or "")
        channel_id = str(event.get("channel") or "")
        text = (event.get("text") or "").strip()
        if not user or not text:
            return None
        if channel_id:
            self._targets[user] = channel_id

        reply = handler(
            InboundMessage(
                channel=self.name,
                external_id=user,
                text=text,
                display_name=str(event.get("username") or ""),
                reply_to=channel_id,
                metadata={"canal_slack": channel_id},
            )
        )
        self.send(user, reply.text)
        return reply

    # ---- saída --------------------------------------------------------
    def send(self, external_id: str, text: str, *, reply_to: str = "") -> dict:
        target = reply_to or self._targets.get(external_id, "")
        if not self.token or not target:
            return {"ok": False, "error": "token ou canal de destino ausente"}
        client = self._client
        if client is None:
            import httpx

            client = httpx
        response = client.post(
            f"{API}/chat.postMessage",
            json={"channel": target, "text": text},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=15.0,
        )
        data = response.json()
        return data if isinstance(data, dict) else {"ok": False, "error": "resposta inesperada"}

    def describe(self) -> dict[str, Any]:
        return {
            "nome": self.name,
            "tipo": self.kind,
            "token_configurado": bool(self.token),
            "assinatura_configurada": bool(self.signing_secret),
        }


__all__ = ["API", "SlackChannel", "signature_for", "verify_signature"]
