"""Versionamento de artefatos: snapshot do conteúdo para poder voltar.

Rollback sem snapshot é arqueologia. Aqui cada versão é o conteúdo completo do
artefato no momento do release — restaurar é escrever de volta e recarregar.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..core.errors import ConfigError
from ..dev.workbench import Workbench
from ..domain.release import ArtifactVersion

ENVIRONMENT_LINE = re.compile(r"^environment:\s*.*$", re.MULTILINE)


class VersionStore:
    """Lê, guarda e restaura o conteúdo dos artefatos do workspace."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    # ---- caminhos -----------------------------------------------------
    @property
    def workspace(self) -> Path:
        return Path(self.runtime.settings.workspace)

    def path_for(self, kind: str, name: str) -> Path:
        from ..core.paths import ensure_inside

        return ensure_inside(self.workspace, Path(Workbench.target_for(kind, name)))

    def content_of(self, kind: str, name: str) -> str:
        path = self.path_for(kind, name)
        if not path.exists():
            raise ConfigError(f"artefato não encontrado: {kind}:{name} ({path.relative_to(self.workspace)})")
        return path.read_text(encoding="utf-8")

    # ---- versões ------------------------------------------------------
    def versions(self, kind: str, name: str) -> list[ArtifactVersion]:
        rows = self.runtime.versions.list(kind=kind, name=name, limit=100)
        return sorted(rows, key=lambda item: item.revision, reverse=True)

    def current(self, kind: str, name: str) -> ArtifactVersion | None:
        rows = self.versions(kind, name)
        return rows[0] if rows else None

    def snapshot(
        self,
        kind: str,
        name: str,
        *,
        actor: str = "cli",
        note: str = "",
        release_id: str | None = None,
    ) -> ArtifactVersion:
        """Guarda o conteúdo atual — só se ele mudou (versão é conteúdo)."""

        content = self.content_of(kind, name)
        fingerprint = ArtifactVersion.fingerprint_of(content)
        last = self.current(kind, name)
        if last and last.fingerprint == fingerprint:
            return last

        revision = (last.revision + 1) if last else 1
        version = _declared_version(kind, content) or f"r{revision}"
        artifact = ArtifactVersion(
            id=f"{kind}:{name}@{revision}",
            kind=kind,
            name=name,
            revision=revision,
            version=version,
            content=content,
            fingerprint=fingerprint,
            environment=_declared_environment(kind, content),
            created_by=actor,
            release_id=release_id,
            note=note,
        )
        saved = self.runtime.versions.save(artifact)
        self.runtime.audit.record(
            "artifact.versioned",
            actor=actor,
            environment=str(artifact.environment),
            payload={
                "artifact": saved.id,
                "kind": kind,
                "name": name,
                "revision": revision,
                "version": version,
                "fingerprint": fingerprint,
                "release": release_id,
            },
        )
        return saved

    def restore(self, kind: str, name: str, revision: int, *, actor: str = "cli", note: str = "") -> ArtifactVersion:
        """Volta o artefato para uma revisão conhecida e recarrega o Runtime."""

        target = next(
            (item for item in self.versions(kind, name) if item.revision == revision),
            None,
        )
        if target is None:
            raise ConfigError(f"revisão {revision} de {kind}:{name} não existe")

        path = self.path_for(kind, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(target.content, encoding="utf-8")
        self._reload(kind, name)
        self.runtime.audit.record(
            "artifact.versioned",
            actor=actor,
            environment=str(target.environment),
            payload={
                "artifact": target.id,
                "action": "restaurada",
                "revision": revision,
                "fingerprint": target.fingerprint,
                "note": note,
            },
        )
        return target

    def set_environment(self, kind: str, name: str, environment: str, *, actor: str = "cli") -> None:
        """Promoção muda o ambiente declarado no próprio artefato."""

        path = self.path_for(kind, name)
        content = self.content_of(kind, name)
        if kind == "tool":
            # ferramenta não declara ambiente em YAML: fica registrado no release
            return
        if ENVIRONMENT_LINE.search(content):
            updated = ENVIRONMENT_LINE.sub(f"environment: {environment}", content, count=1)
        else:
            updated = content.rstrip("\n") + f"\nenvironment: {environment}\n"
        path.write_text(updated, encoding="utf-8")
        self._reload(kind, name)
        self.runtime.audit.record(
            "release.deployed",
            actor=actor,
            environment=environment,
            payload={"kind": kind, "name": name, "ambiente": environment},
        )

    # ---- interno ------------------------------------------------------
    def _reload(self, kind: str, name: str) -> None:
        runtime = self.runtime
        if kind == "agent":
            runtime.sync_agents()
            runtime.reload_agents()
        elif kind == "workflow":
            runtime._load_workflows()
        elif kind == "policy":
            runtime.sync_policies()
        elif kind == "tool":
            runtime.workbench.reload_tools()


def _declared_version(kind: str, content: str) -> str:
    if kind == "tool":
        return ""
    try:
        raw = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        return ""
    if isinstance(raw, dict):
        return str(raw.get("version") or "")
    return ""


def _declared_environment(kind: str, content: str) -> str:
    if kind == "tool":
        return "development"
    try:
        raw = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        return "development"
    if isinstance(raw, dict):
        return str(raw.get("environment") or "development")
    return "development"


__all__ = ["VersionStore"]
