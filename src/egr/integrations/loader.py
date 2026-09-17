"""Carrega `integrations/*.yaml` (a declaração é a fonte da verdade)."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..core.errors import ConfigError
from ..domain.integration import Integration


def load_integration_dir(directory: Path) -> list[Integration]:
    if not directory.exists():
        return []
    found: list[Integration] = []
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
        items = raw.get("integrations", [raw]) if isinstance(raw, dict) else raw
        for item in items:
            if isinstance(item, dict):
                found.append(Integration.model_validate(item))
    return found


__all__ = ["load_integration_dir"]
