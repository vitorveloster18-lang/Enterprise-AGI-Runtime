"""Criptografia autenticada do cofre (AEGIS-EGR1).

O núcleo do EGR é 100% Python e depende apenas da biblioteca padrão (ADR-018).
Como a stdlib não expõe AES, o envelope usa uma construção clássica e
conservadora — **encrypt-then-MAC sobre um keystream de HMAC-SHA256**:

    chave_mestra ──scrypt(salt)──> (k_fluxo, k_mac)
    nonce aleatório (16 bytes)
    cifra  = texto ⊕ HMAC-SHA256(k_fluxo, nonce || contador)
    tag    = HMAC-SHA256(k_mac, versão || nonce || cifra)
    envelope = "EGR1.<nonce>.<cifra>.<tag>"   (base64url)

Propriedades que importam para o Runtime:

* **Sigilo** — sem k_fluxo o keystream é indistinguível de aleatório.
* **Integridade** — qualquer alteração do envelope derruba a tag; a verificação
  usa `hmac.compare_digest` (tempo constante) e **falha fechada**.
* **Derivação** — `scrypt` (N=2^14, r=8, p=1) com salt por segredo, então um
  vazamento de um segredo não enfraquece os demais.
* **Versionado** — o prefixo `EGR1` permite trocar o algoritmo (ex.: AES-GCM
  via `cryptography`) e rotacionar sem quebrar leitura do formato antigo.

Limites honestos: implementação própria, sem AES em hardware, e sem rotação de
chave em memória protegida. Para V1 local/on-premise é adequado; o formato é
versionado justamente para que um backend `cryptography` (Fase 12+) assuma.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from ..core.errors import VaultError

ENVELOPE_VERSION = "EGR1"
NONCE_SIZE = 16
SALT_SIZE = 16
KEY_SIZE = 32
TAG_SIZE = 32

# scrypt: 128 * N * r = 16 MiB — dentro do limite padrão de memória do CPython.
_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1


def new_salt(size: int = SALT_SIZE) -> bytes:
    return os.urandom(size)


def derive_key(master: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Deriva (chave_de_fluxo, chave_de_mac) a partir da chave mestra."""

    material = hashlib.scrypt(
        master,
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=KEY_SIZE * 2,
    )
    return material[:KEY_SIZE], material[KEY_SIZE:]


def new_master_key() -> bytes:
    return os.urandom(KEY_SIZE)


def key_id(master: bytes) -> str:
    """Identificador público da chave (nunca o segredo em si)."""

    return f"key_{hashlib.sha256(master).hexdigest()[:12]}"


def _keystream(stream_key: bytes, nonce: bytes, size: int) -> bytes:
    blocks = []
    produced = 0
    counter = 0
    while produced < size:
        blocks.append(hmac.new(stream_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        produced += 32
        counter += 1
    return b"".join(blocks)[:size]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def encrypt(plaintext: str | bytes, master: bytes, salt: bytes | None = None) -> tuple[str, bytes]:
    """Cifra `plaintext` e devolve (envelope, salt)."""

    salt = salt or new_salt()
    stream_key, mac_key = derive_key(master, salt)
    nonce = os.urandom(NONCE_SIZE)
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    ciphertext = bytes(a ^ b for a, b in zip(data, _keystream(stream_key, nonce, len(data)), strict=False))
    tag = hmac.new(mac_key, f"{ENVELOPE_VERSION}.".encode() + nonce + ciphertext, hashlib.sha256).digest()
    envelope = f"{ENVELOPE_VERSION}.{_b64(nonce)}.{_b64(ciphertext)}.{_b64(tag)}"
    return envelope, salt


def decrypt(envelope: str, master: bytes, salt: bytes) -> bytes:
    """Abre o envelope. Qualquer adulteração ou chave errada ⇒ VaultError."""

    parts = envelope.split(".")
    if len(parts) != 4 or parts[0] != ENVELOPE_VERSION:
        raise VaultError(f"envelope inválido (versão esperada: {ENVELOPE_VERSION})")
    try:
        nonce = _unb64(parts[1])
        ciphertext = _unb64(parts[2])
        tag = _unb64(parts[3])
    except (ValueError, TypeError) as exc:
        raise VaultError("envelope corrompido (base64 inválido)") from exc

    stream_key, mac_key = derive_key(master, salt)
    expected = hmac.new(mac_key, f"{ENVELOPE_VERSION}.".encode() + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise VaultError("falha de autenticação: envelope adulterado ou chave mestra incorreta")

    return bytes(a ^ b for a, b in zip(ciphertext, _keystream(stream_key, nonce, len(ciphertext)), strict=False))


def decrypt_text(envelope: str, master: bytes, salt: bytes) -> str:
    return decrypt(envelope, master, salt).decode("utf-8")


__all__ = [
    "ENVELOPE_VERSION",
    "decrypt",
    "decrypt_text",
    "derive_key",
    "encrypt",
    "key_id",
    "new_master_key",
    "new_salt",
]
