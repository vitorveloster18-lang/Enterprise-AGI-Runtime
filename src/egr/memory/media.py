"""Lacuna 5b: memória multimodal — imagem e áudio entram, o binário não.

A memória da empresa não é só texto: tem print de erro, foto de equipamento,
áudio de reunião. Guardar isso **dentro** do banco é errado (incha, não dá para
inspecionar e vira vazamento silencioso). Guardar o binário solto em qualquer
lugar também.

Como funciona:

- o arquivo vai para `artifacts/media/<ano>/<impressão>.<ext>` — fora do banco,
  dentro do workspace, com impressão digital SHA-256;
- o tipo é conferido pelos **bytes mágicos**, não pela extensão (renomear
  `exe` para `png` não engana);
- o que fica **buscável** é o texto declarado junto: legenda, transcrição,
  descrição. Sem legenda, o registro continua existindo (recuperável por
  metadado e filtro), mas diz `[sem legenda]` em vez de fingir que entendeu a
  imagem;
- o binário nunca é vetorizado e nunca vai para o contexto do modelo: o que o
  agente recebe é o texto e a referência (caminho, tipo, tamanho, impressão).

**O que não existe:** visão computacional e transcrição local. Quem descreve o
conteúdo é quem escreveu (humano ou modelo) — e essa descrição é declarada, não
inventada pelo Runtime.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.errors import ConfigError
from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.memory import MediaAsset

#: assinatura -> (mime, extensão, modalidade)
SIGNATURES: tuple[tuple[bytes, str, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png", "imagem"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg", "imagem"),
    (b"RIFF", "image/webp", "webp", "imagem"),
    (b"GIF87a", "image/gif", "gif", "imagem"),
    (b"GIF89a", "image/gif", "gif", "imagem"),
    (b"ID3", "audio/mpeg", "mp3", "áudio"),
    (b"\xff\xfb", "audio/mpeg", "mp3", "áudio"),
    (b"OggS", "audio/ogg", "ogg", "áudio"),
    (b"%PDF-", "application/pdf", "pdf", "documento"),
)
WAV_MARK = b"WAVE"
#: bytes que denunciam binário (texto não tem byte nulo nem controles)
CONTROL_BYTES = bytes([*range(0, 8), 11, *range(14, 32)])

#: o que é aceito por padrão (configurável por `memory.media_mimes`)
DEFAULT_MIMES = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "application/pdf",
    "text/plain",
)


def sniff(payload: bytes) -> tuple[str, str, str] | None:
    """Tipo real pelo conteúdo. Devolve (mime, extensão, modalidade)."""

    if payload.startswith(b"RIFF") and payload[8:12] == WAV_MARK:
        return "audio/wav", "wav", "áudio"
    for signature, mime, extension, modality in SIGNATURES:
        if payload.startswith(signature):
            return mime, extension, modality
    # só é texto se for texto de verdade: byte nulo e controle denunciam binário
    if any(byte in payload for byte in CONTROL_BYTES):
        return None
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "text/plain", "txt", "texto"


class MediaStore:
    """Guarda o binário, devolve a referência. Nada mais, nada menos."""

    def __init__(self, workspace: Path | str, *, config: Any = None):
        self.workspace = Path(workspace)
        self.config = config

    # ---- paths --------------------------------------------------------
    @property
    def root(self) -> Path:
        return self.workspace / "artifacts" / "media"

    @property
    def limit(self) -> int:
        return int(getattr(self.config, "max_media_bytes", 5 * 1024 * 1024) or 0)

    @property
    def allowed(self) -> tuple[str, ...]:
        configured = getattr(self.config, "media_mimes", None)
        return tuple(configured) if configured else DEFAULT_MIMES

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "media_enabled", True))

    # ---- escrita ------------------------------------------------------
    def store(
        self,
        payload: bytes | str,
        *,
        filename: str = "",
        caption: str = "",
        namespace: str = "default",
        actor: str = "cli",
    ) -> MediaAsset:
        """Valida, grava e devolve a referência (sem conteúdo na memória)."""

        if not self.enabled:
            raise ConfigError("memória multimodal está desligada (`memory.media_enabled: false`)")

        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        if not raw:
            raise ConfigError("arquivo vazio: nada a memorizar")
        if self.limit and len(raw) > self.limit:
            raise ConfigError(
                f"arquivo de {len(raw)} bytes passa do teto de {self.limit} bytes "
                "(`memory.max_media_bytes`)"
            )

        detected = sniff(raw)
        if detected is None:
            raise ConfigError(
                "tipo não reconhecido pelos bytes (só entra imagem, áudio, PDF ou texto): "
                f"'{filename or 'sem nome'}'"
            )
        mime, extension, modality = detected
        if mime not in self.allowed:
            raise ConfigError(f"tipo '{mime}' fora da lista branca ({', '.join(self.allowed)})")

        fingerprint = hashlib.sha256(raw).hexdigest()
        created = utcnow()
        target_dir = self.root / str(created.year)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{fingerprint[:32]}.{extension}"
        if not target.exists():
            target.write_bytes(raw)

        asset = MediaAsset(
            id=new_id("media"),
            namespace=namespace,
            filename=(filename or f"midia.{extension}").strip(),
            mime=mime,
            size=len(raw),
            fingerprint=fingerprint,
            modality=modality,
            path=str(target.relative_to(self.workspace)),
            caption=caption.strip(),
            created_by=actor,
            created_at=created,
        )
        return asset

    # ---- leitura ------------------------------------------------------
    def read(self, asset: MediaAsset) -> bytes:
        """O binário, para quem tem permissão de ler (nunca vai para o modelo)."""

        path = self.workspace / asset.path
        if not path.exists():
            raise ConfigError(f"mídia {asset.id} sumiu do disco: {asset.path}")
        return path.read_bytes()

    def disk_usage(self) -> int:
        if not self.root.exists():
            return 0
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def status(self) -> dict[str, Any]:
        return {
            "ativa": self.enabled,
            "teto_bytes": self.limit,
            "tipos": list(self.allowed),
            "disco_bytes": self.disk_usage(),
        }


def describe(asset: MediaAsset) -> str:
    """Uma linha para contexto/CLI: tipo, nome, tamanho e o que foi declarado."""

    size = f"{asset.size / 1024:.0f} kB" if asset.size >= 1024 else f"{asset.size} B"
    base = f"[{asset.modality} · {asset.filename} · {size} · {asset.mime}]"
    return f"{base} {asset.caption or '[sem legenda]'}"


def since(value: datetime | str | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):  # pragma: no cover - leitura de JSON antigo
        return value
    return value.isoformat()


__all__ = ["DEFAULT_MIMES", "MediaStore", "describe", "sniff"]
