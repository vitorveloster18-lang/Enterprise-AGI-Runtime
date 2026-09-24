"""Lacuna 9b: assinatura do release — o que foi aprovado é o que vai.

Promoção aprovada por nome ("pode subir o agente X") é conversa; promoção
aprovada por **conteúdo** é garantia. A assinatura cobre o manifesto canônico do
release: itens, versões, impressões digitais, ambiente de destino e evidência.
Mudou qualquer pedaço depois de assinado, a verificação falha — e diz qual
impressão esperava e qual encontrou.

A chave é a **mestra do workspace** (a mesma do cofre): nada de chave nova para
cuidar, nada de segredo viajando. Só o `key_id` aparece no release.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from ..core.hashing import canonical_json
from ..domain.release import Release, ReleaseSignature
from ..security.keystore import MasterKeyStore

ALGORITHM = "hmac-sha256"


def manifest(release: Release) -> dict[str, Any]:
    """O que está sendo promovido, em ordem estável — assinável e comparável."""

    return {
        "release": release.id,
        "destino": str(release.target),
        "itens": [
            {
                "artefato": item.key,
                "versão": item.version,
                "revisão": item.revision,
                "impressão": item.fingerprint,
                "de": str(item.from_environment),
                "para": str(item.to_environment),
            }
            for item in release.items
        ],
        "evidência": sorted(release.evidence),
        "criado_por": release.created_by,
    }


def manifest_hash(release: Release) -> str:
    return hashlib.sha256(canonical_json(manifest(release)).encode("utf-8")).hexdigest()


def sign(runtime: Any, release: Release, *, actor: str = "human:cli") -> ReleaseSignature:
    """Assina o manifesto com a chave mestra do workspace."""

    key = _master_key(runtime)
    digest = manifest_hash(release)
    value = hmac.new(key.raw, digest.encode("utf-8"), hashlib.sha256).hexdigest()
    signature = ReleaseSignature(
        release_id=release.id,
        manifest_hash=digest,
        algorithm=ALGORITHM,
        key_id=key.key_id,
        value=value,
        signed_by=actor,
    )
    release.signature = signature
    runtime.audit.record(
        "release.signed",
        actor=actor,
        environment=str(release.target),
        payload={
            "release": release.id,
            "impressão": digest[:16],
            "chave": key.key_id,
            "algoritmo": ALGORITHM,
            "destino": str(release.target),
        },
    )
    return signature


def verify(runtime: Any, release: Release) -> tuple[bool, str]:
    """Confere assinatura **e** conteúdo: mudou o release, a assinatura caiu."""

    signature = release.signature
    if signature is None or not signature.value:
        return False, "release sem assinatura"
    try:
        key = _master_key(runtime)
    except Exception as exc:
        return False, f"chave indisponível para verificar: {exc}"

    current = manifest_hash(release)
    if current != signature.manifest_hash:
        return (
            False,
            f"release mudou depois de assinado (impressão assinada {signature.manifest_hash[:12]}, "
            f"atual {current[:12]})",
        )
    if signature.key_id and signature.key_id != key.key_id:
        return False, f"assinado com outra chave ({signature.key_id[:12]} ≠ {key.key_id[:12]})"
    expected = hmac.new(key.raw, current.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.value or ""):
        return False, "assinatura não confere: conteúdo ou chave diferente da usada para assinar"
    return True, f"válida · {ALGORITHM} · chave {key.key_id[:12]} · {signature.signed_by}"


def _master_key(runtime: Any):
    store = runtime.keystore if isinstance(runtime.keystore, MasterKeyStore) else MasterKeyStore(
        runtime.settings.workspace
    )
    return store.require()


__all__ = ["ALGORITHM", "manifest", "manifest_hash", "sign", "verify"]
