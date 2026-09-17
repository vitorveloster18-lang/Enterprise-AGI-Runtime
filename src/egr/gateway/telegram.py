"""Canal Telegram: long polling (ou webhook) sobre a Bot API.

Sem SDK, sem dependência nova: `httpx` (ou qualquer cliente injetado com
`.post(url, json=..., timeout=...)`). O token nunca vem do `egr.yaml` — ele é
lido de uma variável de ambiente (`bot_token_env`).
"""

from __future__ import annotations

import os
from typing import Any

from ..domain.channel import InboundMessage
from .channels import BaseChannel, Handler

API = "https://api.telegram.org"


class TelegramChannel(BaseChannel):
    """Pergunta à Bot API o que chegou e devolve a resposta do Gateway."""

    kind = "telegram"

    def __init__(self, name: str, config: Any = None, *, client: Any = None, token: str | None = None):
        super().__init__(name, config)
        self._client = client
        self._token = token
        self._offset = 0

    # ---- credencial ---------------------------------------------------
    @property
    def token(self) -> str:
        if self._token:
            return self._token
        env = self.config.bot_token_env if self.config else "EGR_TELEGRAM_TOKEN"
        return os.environ.get(env, "")

    @property
    def webhook_secret(self) -> str:
        env = self.config.webhook_secret_env if self.config else ""
        return os.environ.get(env, "") if env else ""

    # ---- transporte ---------------------------------------------------
    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    def _call(self, method: str, payload: dict | None = None, *, timeout: float = 30.0) -> dict:
        if not self.token:
            return {"ok": False, "error": "token de telegram não configurado"}
        client = self._client
        if client is None:
            import httpx

            client = httpx
        response = client.post(self._url(method), json=payload or {}, timeout=timeout)
        data = response.json()
        return data if isinstance(data, dict) else {"ok": False, "error": "resposta inesperada"}

    def me(self) -> dict:
        return self._call("getMe")

    # ---- entrada ------------------------------------------------------
    def poll_once(self, handler: Handler) -> int:
        data = self._call(
            "getUpdates",
            {"offset": self._offset, "timeout": int(getattr(self.config, "polling_timeout", 25) or 25)},
        )
        updates = data.get("result") if isinstance(data, dict) else None
        if not isinstance(updates, list):
            return 0
        handled = 0
        for update in updates:
            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
            reply = self.handle_update(update, handler)
            handled += 1 if reply is not None else 0
        return handled

    def handle_update(self, update: dict, handler: Handler):
        """Trata um update (polling ou webhook). Ignora o que não é texto."""

        message = update.get("message") or update.get("edited_message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        external_id = str(chat.get("id") or "")
        if not text or not external_id:
            return None
        sender = message.get("from") or {}
        reply = handler(
            InboundMessage(
                channel=self.name,
                external_id=external_id,
                text=text,
                display_name=sender.get("first_name") or sender.get("username") or "",
                metadata={"update": update.get("update_id")},
            )
        )
        self.send(external_id, reply.text)
        return reply

    # ---- saída --------------------------------------------------------
    def send(self, external_id: str, text: str, *, reply_to: str = "") -> dict:
        return self._call("sendMessage", {"chat_id": external_id, "text": text})

    def describe(self) -> dict[str, Any]:
        return {
            "nome": self.name,
            "tipo": self.kind,
            "token_configurado": bool(self.token),
            "webhook": bool(self.webhook_secret),
        }


__all__ = ["API", "TelegramChannel"]
