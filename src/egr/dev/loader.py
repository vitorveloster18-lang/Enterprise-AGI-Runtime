"""Carregamento das ferramentas do workspace (`tools/*.py`).

Uma ferramenta criada por proposta aprovada tem de continuar segura quando o
Runtime reinicia — e o que está no disco pode ter sido editado à mão. Por isso
o carregamento **revalida** o código com a mesma análise estática usada na
proposta: arquivo que não passa é recusado, com evento no ledger, e nunca
chega ao registry.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

from ..domain.enums import EventType
from ..tools.protocol import Tool
from .validators import validate_tool_source

SKIP_PREFIXES = ("_", ".")


@dataclass
class ToolLoadReport:
    loaded: list[Tool]
    rejected: list[dict]

    @property
    def names(self) -> list[str]:
        return [tool.spec.name for tool in self.loaded]


def load_tool_dir(
    directory: Path,
    *,
    audit=None,
    environment: str = "development",
) -> ToolLoadReport:
    """Importa `tools/*.py`, validando cada módulo antes de executá-lo."""

    loaded: list[Tool] = []
    rejected: list[dict] = []
    if not directory.exists():
        return ToolLoadReport(loaded, rejected)

    for path in sorted(directory.glob("*.py")):
        if path.name.startswith(SKIP_PREFIXES):
            continue
        source = path.read_text(encoding="utf-8")
        namespace = f"{path.stem}."
        problems = [
            check.detail
            for check in validate_tool_source(source, namespace=namespace)
            if not check.ok and check.level == "error"
        ]
        if problems:
            rejected.append({"file": path.name, "problems": problems})
            if audit is not None:
                audit.record(
                    EventType.DEV_TOOL_REJECTED,
                    actor="runtime",
                    environment=environment,
                    payload={"file": str(path), "problems": problems[:5]},
                )
            continue

        try:
            tools = _import_tools(path)
        except Exception as exc:  # pragma: no cover - defensivo
            rejected.append({"file": path.name, "problems": [f"falha ao importar: {type(exc).__name__}: {exc}"]})
            if audit is not None:
                audit.record(
                    EventType.DEV_TOOL_REJECTED,
                    actor="runtime",
                    environment=environment,
                    payload={"file": str(path), "problems": [f"{type(exc).__name__}: {exc}"]},
                )
            continue

        for tool in tools:
            loaded.append(tool)
            if audit is not None:
                audit.record(
                    EventType.DEV_TOOL_LOADED,
                    actor="runtime",
                    environment=environment,
                    payload={
                        "tool": tool.spec.name,
                        "file": str(path),
                        "risk": str(tool.spec.risk),
                        "side_effects": tool.spec.side_effects,
                    },
                )
    return ToolLoadReport(loaded, rejected)


def find_tool_source(workspace: Path, tool_name: str) -> tuple[Path, str] | None:
    """Onde está o código de uma ferramenta do workspace (ou None se for builtin)."""

    directory = Path(workspace) / "tools"
    if not directory.exists():
        return None
    for path in sorted(directory.glob("*.py")):
        content = path.read_text(encoding="utf-8")
        if f'name="{tool_name}"' in content or f"name='{tool_name}'" in content:
            return path, content
    return None


def _import_tools(path: Path) -> list[Tool]:
    module_name = f"egr_workspace_tool_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensivo
        return []
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)

    tools: list[Tool] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, Tool) and obj is not Tool and obj.__module__ == module_name:
            tools.append(obj())
    return tools


__all__ = ["ToolLoadReport", "find_tool_source", "load_tool_dir"]
