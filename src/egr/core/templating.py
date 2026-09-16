"""Interpolação mínima e segura para workflows: `{{ steps.s1.answer }}`.

Sem Jinja, sem eval: só leitura de caminho pontuado em um dicionário. O que não
existe não levanta exceção — vira string vazia e fica óbvio no objetivo gerado.
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")

MISSING = ""


def lookup(context: dict, path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return MISSING
    return current


def render(value: Any, context: dict) -> Any:
    """Aplica o template em strings, recursivamente em listas e dicionários."""

    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda match: str(lookup(context, match.group(1))), value)
    if isinstance(value, list):
        return [render(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    return value


def missing_paths(value: Any, context: dict) -> list[str]:
    """Caminhos referenciados que não existem no contexto (para validação)."""

    found: list[str] = []
    if isinstance(value, str):
        for match in _PLACEHOLDER.finditer(value):
            path = match.group(1)
            if path not in context and lookup(context, path) == MISSING:
                found.append(path)
    elif isinstance(value, list):
        for item in value:
            found.extend(missing_paths(item, context))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(missing_paths(item, context))
    return sorted(set(found))


__all__ = ["MISSING", "lookup", "missing_paths", "render"]
