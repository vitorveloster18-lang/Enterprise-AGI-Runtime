"""Lacuna 10b: anexo é conteúdo, e conteúdo entra governado.

O canal é só o envelope. Quem decide se um arquivo entra, onde ele dorme e o
que a task pode ver é este serviço — com as mesmas quatro perguntas de sempre:
**quem** mandou, **o que** é, **onde** fica e **o que fica registrado**.

Regras, em ordem:

1. tipo e tamanho vêm de lista branca (o que não está na lista, não entra);
2. o conteúdo só é baixado **depois** da conferência (o canal oferece `fetch`,
   o Runtime decide se chama);
3. o arquivo dorme dentro do workspace (`artifacts/inbox/...`), nunca fora;
4. o que a task recebe é o caminho e (se for texto) um trecho **redigido**;
5. recusar também é resultado: vira anexo `rejected` com motivo e evento.

Na saída vale o espelho: só sai arquivo de raízes declaradas, com teto de
tamanho e caminho conferido contra o workspace.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.ids import new_id
from ..core.paths import ensure_inside
from ..domain.channel import Attachment, InboundAttachment
from ..domain.enums import AttachmentStatus, EventType
from ..security.redaction import redact_text

TEXTUAL = {".txt", ".md", ".csv", ".json", ".yml", ".yaml"}
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    """Nome de arquivo sem travessia, sem caractere esquisito, sem espaço."""

    candidate = _SAFE.sub("-", Path(name or "anexo").name).strip(".-")
    return (candidate or "anexo")[:80]


class AttachmentService:
    """Onde um arquivo de fora vira arquivo do workspace — ou não entra."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.repository = runtime.gateway_attachments

    # ---- configuração -------------------------------------------------
    @property
    def config(self):
        return self.runtime.settings.config.gateway.attachments

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled and self.runtime.settings.config.gateway.enabled)

    def channel_allows(self, name: str) -> bool:
        config = self.runtime.channels.config_for(name)
        return bool(getattr(config, "allow_attachments", True))

    # ---- entrada ------------------------------------------------------
    def receive(
        self,
        channel: str,
        external_id: str,
        pending: InboundAttachment,
        *,
        actor: str = "",
    ) -> Attachment:
        """Aceita, guarda e registra — ou recusa dizendo por quê."""

        attachment = Attachment(
            id=new_id("attach"),
            channel=channel,
            external_id=external_id,
            name=safe_name(pending.name),
            mime=pending.mime or "",
            size=pending.size or 0,
            remote_ref=pending.remote_ref or "",
        )

        if not self.enabled:
            return self._refuse(attachment, "anexos desabilitados na configuração", actor=actor)
        if not self.channel_allows(channel):
            return self._refuse(attachment, f"canal '{channel}' não aceita anexos", actor=actor)

        allowed, reason = self._content_policy(pending, attachment)
        if not allowed:
            return self._refuse(attachment, reason, actor=actor)

        content = pending.content
        if content is None and pending.fetch is not None:
            try:
                content = pending.fetch()
            except Exception as exc:  # canal caiu, token expirou, arquivo sumiu
                return self._refuse(attachment, f"falha ao baixar: {exc}", actor=actor)
        if content is None:
            return self._refuse(attachment, "canal não entregou o conteúdo", actor=actor)
        if len(content) > self.config.max_bytes:
            return self._refuse(
                attachment,
                f"tamanho {len(content)} B excede o limite de {self.config.max_bytes} B",
                actor=actor,
            )

        stored = self._write(channel, external_id, attachment.name, content)
        attachment.path = str(stored.relative_to(self.runtime.settings.workspace))
        attachment.size = len(content)
        attachment.checksum = _digest(content)
        attachment.status = AttachmentStatus.STORED
        attachment.preview, attachment.text_chars = self._preview(stored, content)
        self.repository.save(attachment)
        self.runtime.audit.record(
            EventType.GATEWAY_ATTACHMENT,
            actor=actor or f"{channel}:{external_id}",
            environment=str(self.runtime.settings.environment),
            payload={
                "canal": channel,
                "anexo": attachment.id,
                "nome": attachment.name,
                "tipo": attachment.mime or "-",
                "tamanho": attachment.size,
                "caminho": attachment.path,
                "impressão": attachment.checksum,
                "situação": "stored",
            },
        )
        return attachment

    def receive_many(
        self,
        channel: str,
        external_id: str,
        pendings: list[InboundAttachment],
        *,
        actor: str = "",
    ) -> list[Attachment]:
        """Uma mensagem pode trazer vários arquivos — até o limite declarado."""

        results: list[Attachment] = []
        for index, pending in enumerate(pendings):
            if index >= self.config.max_files:
                attachment = Attachment(
                    id=new_id("attach"),
                    channel=channel,
                    external_id=external_id,
                    name=safe_name(pending.name),
                    mime=pending.mime or "",
                    size=pending.size or 0,
                    remote_ref=pending.remote_ref or "",
                )
                results.append(
                    self._refuse(
                        attachment,
                        f"limite de {self.config.max_files} arquivo(s) por mensagem",
                        actor=actor,
                    )
                )
                continue
            results.append(self.receive(channel, external_id, pending, actor=actor))
        return results

    def _content_policy(self, pending: InboundAttachment, attachment: Attachment) -> tuple[bool, str]:
        """Tipo e tamanho por lista branca: o que não foi declarado, não entra."""

        config = self.config
        if pending.size and pending.size > config.max_bytes:
            return False, f"tamanho declarado {pending.size} B excede o limite de {config.max_bytes} B"
        extension = pending.extension or Path(attachment.name).suffix.lower()
        if config.allowed_extensions and extension and extension not in {
            item.lower() for item in config.allowed_extensions
        }:
            return False, f"extensão '{extension or '?'}' fora da lista permitida"
        mime = (pending.mime or "").split(";")[0].strip().lower()
        if config.allowed_mime and mime and mime not in {item.lower() for item in config.allowed_mime}:
            return False, f"tipo '{mime}' fora da lista permitida"
        return True, ""

    def _write(self, channel: str, external_id: str, name: str, content: bytes) -> Path:
        """Escreve dentro do workspace — e confere que continua dentro."""

        root = self.runtime.settings.workspace
        target_dir = root / self.config.inbox / safe_name(channel) / safe_name(external_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        if target.exists():
            stem, suffix = target.stem, target.suffix
            counter = 2
            while target.exists():
                target = target_dir / f"{stem}-{counter}{suffix}"
                counter += 1
        target.write_bytes(content)
        return ensure_inside(root, target)

    def _preview(self, path: Path, content: bytes) -> tuple[str, int]:
        """Texto de arquivos texto: contexto para a task, redigido e curto."""

        limit = self.config.extract_chars
        if limit <= 0 or path.suffix.lower() not in TEXTUAL:
            return "", 0
        try:
            text = content.decode("utf-8", "replace")
        except Exception:
            return "", 0
        if self.config.redact:
            text = redact_text(text)
        return text[:limit], min(len(text), limit)

    def _refuse(self, attachment: Attachment, reason: str, *, actor: str = "") -> Attachment:
        attachment.status = AttachmentStatus.REJECTED
        attachment.reason = reason
        attachment.preview = ""
        self.repository.save(attachment)
        self.runtime.audit.record(
            EventType.GATEWAY_ATTACHMENT_REJECTED,
            actor=actor or f"{attachment.channel}:{attachment.external_id}",
            environment=str(self.runtime.settings.environment),
            payload={
                "canal": attachment.channel,
                "anexo": attachment.id,
                "nome": attachment.name,
                "tipo": attachment.mime or "-",
                "tamanho": attachment.size,
                "motivo": reason,
            },
        )
        return attachment

    # ---- contexto para a task -----------------------------------------
    def manifest(self, attachments: list[Attachment]) -> str:
        """O que a task precisa saber sobre os arquivos que vieram com ela."""

        lines = ["Anexos recebidos pelo canal (já salvos no workspace):"]
        for attachment in attachments:
            if not attachment.stored:
                lines.append(f"• {attachment.name} — recusado: {attachment.reason}")
                continue
            line = f"• {attachment.path} ({attachment.mime or 'tipo não declarado'}, {attachment.size} B)"
            lines.append(line)
            if attachment.preview:
                lines.append("  trecho: " + attachment.preview.replace("\n", " ")[:400])
        return "\n".join(lines)

    # ---- saída --------------------------------------------------------
    def outbound(self, relative: str) -> dict[str, Any]:
        """Confere um arquivo do workspace antes de sair pelo canal."""

        if not self.enabled or not self.config.outbound_enabled:
            return {"ok": False, "erro": "saída de arquivos desabilitada na configuração"}
        root = self.runtime.settings.workspace
        try:
            target = ensure_inside(root, root / relative)
        except Exception as exc:
            return {"ok": False, "erro": str(exc)}
        allowed = {str(root / item) for item in self.config.outbound_roots}
        if not any(str(target).startswith(prefix + "/") or str(target) == prefix for prefix in allowed):
            return {"ok": False, "erro": f"'{relative}' fora das raízes permitidas para saída"}
        if not target.exists() or not target.is_file():
            return {"ok": False, "erro": f"arquivo não encontrado: {relative}"}
        size = target.stat().st_size
        if size > self.config.outbound_max_bytes:
            return {"ok": False, "erro": f"arquivo tem {size} B (limite de saída {self.config.outbound_max_bytes} B)"}
        return {
            "ok": True,
            "name": target.name,
            "path": str(target.relative_to(root)),
            "absolute": str(target),
            "size": size,
            "mime": _guess_mime(target),
        }

    # ---- estado -------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "habilitado": self.enabled,
            "limite_bytes": self.config.max_bytes,
            "por_mensagem": self.config.max_files,
            "inbox": self.config.inbox,
            "tipos": len(self.config.allowed_mime),
            "extensões": len(self.config.allowed_extensions),
            "saída": bool(self.config.outbound_enabled),
            "raízes_de_saída": list(self.config.outbound_roots),
            "por_situação": self.repository.stats(),
            "bytes_guardados": self.repository.stored_bytes(),
            "recentes": [item.summary() for item in self.repository.list(limit=5)],
        }


def _guess_mime(path: Path) -> str:
    return {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".csv": "text/csv",
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".yml": "text/yaml",
        ".yaml": "text/yaml",
    }.get(path.suffix.lower(), "application/octet-stream")


def _digest(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


__all__ = ["TEXTUAL", "AttachmentService", "safe_name"]
