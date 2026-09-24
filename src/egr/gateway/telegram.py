"""Canal Telegram: long polling (ou webhook) sobre a Bot API.

Sem SDK, sem dependência nova: `httpx` (ou qualquer cliente injetado com
`.post(url, json=..., timeout=...)`). O token nunca vem do `egr.yaml` — ele é
lido de uma variável de ambiente (`bot_token_env`).
"""

from __future__ import annotations

import os
from typing import Any

from ..domain.channel import InboundAttachment, InboundMessage
from .channels import BaseChannel, Handler

API = "https://api.telegram.org"
#: mídias que o Telegram manda e que o Runtime aceita descrever
MEDIA_KEYS = ("document", "audio", "video", "voice", "animation")


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
        """Trata um update (polling ou webhook): texto, mídia ou os dois."""

        message = update.get("message") or update.get("edited_message") or {}
        text = (message.get("text") or message.get("caption") or "").strip()
        chat = message.get("chat") or {}
        external_id = str(chat.get("id") or "")
        attachments = self.attachments_from(message)
        if not external_id or (not text and not attachments):
            return None
        sender = message.get("from") or {}
        reply = handler(
            InboundMessage(
                channel=self.name,
                external_id=external_id,
                text=text,
                display_name=sender.get("first_name") or sender.get("username") or "",
                metadata={"update": update.get("update_id")},
                attachments=attachments,
            )
        )
        self.deliver(external_id, reply)
        return reply

    # ---- lacuna 10b: anexos ------------------------------------------
    def attachments_from(self, message: dict) -> list[InboundAttachment]:
        """Descreve a mídia recebida — sem baixar nada (quem decide é o Runtime)."""

        found: list[InboundAttachment] = []
        for key in MEDIA_KEYS:
            payload = message.get(key)
            if isinstance(payload, dict) and payload.get("file_id"):
                found.append(self._describe(payload, payload.get("file_name") or f"{key}.bin"))
        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            # o Telegram manda vários tamanhos; o maior é o último
            biggest = max(photos, key=lambda item: int(item.get("file_size") or 0))
            found.append(self._describe(biggest, f"foto-{biggest.get('file_unique_id', 'x')}.jpg", "image/jpeg"))
        return found

    def _describe(self, payload: dict, name: str, mime: str = "") -> InboundAttachment:
        file_id = str(payload.get("file_id") or "")
        return InboundAttachment(
            name=name,
            mime=payload.get("mime_type") or mime or "",
            size=int(payload.get("file_size") or 0),
            remote_ref=file_id,
            fetch=(lambda: self.download(file_id)) if file_id else None,
        )

    def download(self, file_id: str) -> bytes:
        """`getFile` diz o caminho; `downloadFile` traz o conteúdo."""

        path = self.file_path(file_id)
        if not path:
            raise RuntimeError(f"telegram não informou o caminho de {file_id}")
        client = self._client
        if client is None:
            import httpx

            client = httpx
        response = client.get(f"{API}/file/bot{self.token}/{path}", timeout=30.0)
        content = response.content if hasattr(response, "content") else response
        if isinstance(content, str):
            content = content.encode()
        return content

    def file_path(self, file_id: str) -> str:
        data = self._call("getFile", {"file_id": file_id})
        result = data.get("result") if isinstance(data, dict) else None
        return str(result.get("file_path") or "") if isinstance(result, dict) else ""

    # ---- saída --------------------------------------------------------
    def send(self, external_id: str, text: str, *, reply_to: str = "") -> dict:
        return self._call("sendMessage", {"chat_id": external_id, "text": text})

    def send_attachment(
        self,
        external_id: str,
        path: str,
        *,
        name: str = "",
        mime: str = "",
        reply_to: str = "",
    ) -> dict:
        from pathlib import Path

        target = Path(path)
        if not target.exists():
            return {"ok": False, "error": f"arquivo ausente: {path}"}
        if not self.token:
            return {"ok": False, "error": "token de telegram não configurado"}
        client = self._client
        if client is None:
            import httpx

            client = httpx
        response = client.post(
            self._url("sendDocument"),
            data={"chat_id": external_id},
            files={"document": (name or target.name, target.read_bytes(), mime or "application/octet-stream")},
            timeout=60.0,
        )
        data = response.json()
        return data if isinstance(data, dict) else {"ok": False, "error": "resposta inesperada"}

    def describe(self) -> dict[str, Any]:
        return {
            "nome": self.name,
            "tipo": self.kind,
            "token_configurado": bool(self.token),
            "webhook": bool(self.webhook_secret),
        }


__all__ = ["API", "TelegramChannel"]
