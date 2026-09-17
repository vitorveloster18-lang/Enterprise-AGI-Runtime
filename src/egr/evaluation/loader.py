"""Suítes declaradas em YAML (`evaluations/*.yaml`)."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..core.errors import ConfigError
from ..domain.evaluation import EvaluationSuite


def load_suite_file(path: Path) -> list[EvaluationSuite]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if isinstance(raw, dict) and "suites" in raw:
        items = raw["suites"]
    elif isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ConfigError(f"{path}: esperado uma suíte, uma lista ou {{suites: [...]}}")

    suites = []
    for item in items:
        try:
            suites.append(EvaluationSuite.model_validate(item))
        except Exception as exc:
            raise ConfigError(f"{path}: suíte inválida: {exc}") from exc
    return suites


def load_suite_dir(directory: Path) -> list[EvaluationSuite]:
    if not directory.exists():
        return []
    suites: list[EvaluationSuite] = []
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        suites.extend(load_suite_file(path))
    return suites


__all__ = ["load_suite_dir", "load_suite_file"]
