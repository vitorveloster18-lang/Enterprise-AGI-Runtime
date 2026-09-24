"""Rich output helpers for the CLI."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()
error_console = Console(stderr=True)


def info(message: str) -> None:
    console.print(f"[cyan]•[/cyan] {message}")


def success(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def warning(message: str) -> None:
    console.print(f"[yellow]![/yellow] {message}")


def error(message: str) -> None:
    error_console.print(f"[red]✗[/red] {message}")


def secret_line(label: str, value: str) -> None:
    """Imprime um valor sensível sem quebra de linha, corte ou highlight.

    Tokens e segredos longos não podem ser truncados pelo rich: um token
    cortado é um token inútil.
    """

    console.print(f"[cyan]{label}[/cyan] {value}", soft_wrap=True, highlight=False)


def table(title: str, columns: list[str], rows: list[list[Any]], caption: str | None = None) -> None:
    rendered = Table(title=title, caption=caption, header_style="bold cyan")
    for column in columns:
        rendered.add_column(column)
    for row in rows:
        rendered.add_row(*[str(cell) if cell is not None else "-" for cell in row])
    console.print(rendered)


def kv(title: str, data: dict[str, Any]) -> None:
    rendered = Table(title=title, header_style="bold cyan", show_lines=False)
    rendered.add_column("key", style="cyan")
    rendered.add_column("value")
    for key, value in data.items():
        rendered.add_row(str(key), _stringify(value))
    console.print(rendered)


def panel(title: str, content: str, style: str = "cyan") -> None:
    console.print(Panel(content, title=title, border_style=style))


def code(content: str, language: str = "markdown") -> None:
    console.print(Syntax(content, language, theme="ansi_dark", word_wrap=True))


def json_output(data: Any) -> None:
    """Raw JSON on stdout (no rich markup/wrapping) so it can be piped."""

    import sys

    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value)
    return str(value)


def parse_kv(pairs: list[str] | None) -> dict[str, Any]:
    """Parse repeated `--arg key=value` into a typed dict."""

    result: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            result[pair] = True
            continue
        key, value = pair.split("=", 1)
        result[key.strip()] = _coerce(value)
    return result


def _coerce(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if (value.startswith("{") and value.endswith("}")) or (value.startswith("[") and value.endswith("]")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
