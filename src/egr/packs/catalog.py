"""Catálogo: packs do pacote EGR (built-in) + packs do workspace."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import yaml

from ..core.errors import ConfigError
from ..domain.pack import Pack

PACKAGE = "egr.templates.packs"


def load_pack_file(path: Path, *, origin: str = "workspace") -> Pack:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: esperado um objeto com o pack")
    try:
        return Pack.model_validate({**raw, "origin": origin})
    except Exception as exc:
        raise ConfigError(f"{path}: pack inválido: {exc}") from exc


def load_pack_dir(directory: Path, *, origin: str = "workspace") -> list[Pack]:
    if not directory.exists():
        return []
    found: list[Pack] = []
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        found.append(load_pack_file(path, origin=origin))
    return found


def builtin_packs() -> list[Pack]:
    """Packs que o EGR entrega: o catálogo é código, versionado com o Runtime."""

    base = files(PACKAGE)
    found: list[Pack] = []
    for resource in sorted(base.iterdir(), key=lambda item: item.name):
        if resource.is_file() and resource.name.endswith((".yaml", ".yml")):
            text = resource.read_text(encoding="utf-8")
            try:
                raw = yaml.safe_load(text) or {}
            except yaml.YAMLError as exc:  # catálogo quebrado é bug, não config
                raise ConfigError(f"invalid built-in pack {resource.name}: {exc}") from exc
            found.append(Pack.model_validate({**raw, "origin": "builtin"}))
    return found


def available_packs(workspace: Path | None = None) -> list[Pack]:
    """Built-ins + workspace. O workspace tem a palavra final (sobrepõe por id)."""

    packs = {pack.id: pack for pack in builtin_packs()}
    if workspace is not None:
        for pack in load_pack_dir(Path(workspace) / "packs", origin="workspace"):
            packs[pack.id] = pack
    return sorted(packs.values(), key=lambda item: item.id)


__all__ = ["available_packs", "builtin_packs", "load_pack_dir", "load_pack_file"]
