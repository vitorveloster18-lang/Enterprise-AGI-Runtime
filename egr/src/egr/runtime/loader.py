"""Declarative loaders: agents / workflows / policies as source-controlled YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..core.errors import ConfigError
from ..domain.agent import AgentSpec
from ..domain.workflow import Workflow


def load_agent_file(path: Path) -> list[AgentSpec]:
    raw = _read(path)
    if isinstance(raw, dict) and "agents" in raw:
        items = raw["agents"]
    elif isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ConfigError(f"{path}: expected an agent, a list, or {{agents: [...]}}")
    return [AgentSpec.model_validate(item) for item in items]


def load_agent_dir(directory: Path) -> list[AgentSpec]:
    if not directory.exists():
        return []
    specs: list[AgentSpec] = []
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        specs.extend(load_agent_file(path))
    return specs


def load_workflow_dir(directory: Path) -> list[Workflow]:
    if not directory.exists():
        return []
    workflows: list[Workflow] = []
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        raw = _read(path)
        items = raw.get("workflows", [raw]) if isinstance(raw, dict) else raw
        for item in items:
            workflows.append(Workflow.model_validate(item))
    return workflows


def _read(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
