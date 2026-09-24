"""Load policies from source-controlled YAML (policies/*.yaml).

YAML is the definition, the database is the runtime source of truth.
`egr policy sync` moves definitions from disk into the runtime.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..core.errors import ConfigError
from ..domain.policy import Policy


def load_policy_file(path: Path) -> list[Policy]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if isinstance(raw, dict) and "policies" in raw:
        items = raw["policies"]
    elif isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ConfigError(f"{path}: expected a policy, a list of policies, or {{policies: [...]}}")

    return [Policy.model_validate(item) for item in items]


def load_policy_dir(directory: Path) -> list[Policy]:
    if not directory.exists():
        return []
    policies: list[Policy] = []
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        policies.extend(load_policy_file(path))
    return policies
