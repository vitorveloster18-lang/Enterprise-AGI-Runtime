"""Catálogo de comandos da interface.

A interface não reimplementa nada: o catálogo é lido direto do app Typer, então
todo comando que existe na CLI aparece na interface automaticamente — e nada sai
do caminho governado, porque a execução chama a mesma CLI (que chama o Runtime).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import typer
from typer.main import get_command
from typer.testing import CliRunner

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# comandos que derrubariam a interface se rodassem dentro dela
BLOCKED = frozenset({"serve", "tui", "ui"})


@dataclass(frozen=True)
class ParamSpec:
    """Um parâmetro de linha de comando (opção ou argumento)."""

    name: str
    flags: tuple[str, ...]
    kind: str  # option | argument
    required: bool
    default: Any
    help: str
    is_flag: bool
    multiple: bool


@dataclass(frozen=True)
class CommandSpec:
    """Um comando alcançável na interface (ex.: 'task exec')."""

    path: str
    help: str
    params: tuple[ParamSpec, ...]

    @property
    def group(self) -> str:
        return self.path.split(" ", 1)[0]

    @property
    def name(self) -> str:
        return self.path.split(" ")[-1]

    def option(self, name: str) -> ParamSpec | None:
        for param in self.params:
            if param.kind == "option" and param.name == name:
                return param
        return None


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    for line in text.strip().splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _is_option(param) -> bool:
    """Typer novo traz as próprias classes: olhar a forma, não o tipo.

    Opção começa com '-'; argumento vem com o próprio nome em `opts`.
    """

    opts = tuple(getattr(param, "opts", None) or ())
    return any(opt.startswith("-") for opt in opts)


def _param_spec(param) -> ParamSpec | None:
    if _is_option(param):
        flags = tuple(param.opts)
        if param.name in {"help"}:
            return None
        return ParamSpec(
            name=param.name or "",
            flags=flags,
            kind="option",
            required=bool(param.required),
            default=param.default,
            help=_first_line(param.help),
            is_flag=bool(getattr(param, "is_flag", False)),
            multiple=bool(getattr(param, "multiple", False))
            or (param.nargs != 1 and not getattr(param, "is_flag", False)),
        )
    if getattr(param, "nargs", None) is not None:
        return ParamSpec(
            name=param.name or "",
            flags=(),
            kind="argument",
            required=bool(param.required),
            default=None,
            help=_first_line(param.help),
            is_flag=False,
            multiple=bool(param.nargs != 1),
        )
    return None


def build_catalog(app: typer.Typer) -> list[CommandSpec]:
    """Lista todos os comandos do app Typer como fichas navegáveis."""

    root = get_command(app)
    found: list[CommandSpec] = []

    def walk(command, prefix: str = "") -> None:
        subcommands = getattr(command, "commands", None)
        if subcommands:
            for name, sub in sorted(subcommands.items()):
                walk(sub, f"{prefix}{name} ")
            return
        path = prefix.strip()
        if path.split(" ", 1)[0] in BLOCKED:
            return
        params = tuple(
            spec for spec in (_param_spec(param) for param in command.params) if spec
        )
        found.append(CommandSpec(path=path, help=_first_line(command.help), params=params))

    walk(root)
    return sorted(found, key=lambda spec: spec.path)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def _click_command(app: typer.Typer, args: list[str]):
    """Acha o comando que corresponde a `egr <args...>`."""

    command = get_command(app)
    for token in args:
        subcommands = getattr(command, "commands", None) or {}
        if token in subcommands:
            command = subcommands[token]
        else:
            return command
    return command


def accepts_workspace(app: typer.Typer, args: list[str]) -> bool:
    command = _click_command(app, args)
    if command is None:
        return False
    return any(
        _is_option(param) and param.name == "workspace" for param in command.params
    )


def run_command(app: typer.Typer, args: list[str], workspace: str | None = None) -> tuple[int, str]:
    """Executa um comando da CLI capturando a saída (para o painel da interface)."""

    runner = CliRunner()
    final = list(args)
    if workspace and accepts_workspace(app, final) and "--workspace" not in final:
        final += ["--workspace", workspace]
    result = runner.invoke(app, final, catch_exceptions=True)
    output = getattr(result, "output", None)
    if output is None:  # click mais antigo separa stdout
        output = getattr(result, "stdout", "") or ""
    code = result.exit_code if isinstance(result.exit_code, int) else 1
    return code, strip_ansi(output)


def build_args(spec: CommandSpec, values: dict[str, Any]) -> list[str]:
    """Monta a linha de comando a partir do formulário da interface."""

    args: list[str] = list(spec.path.split(" "))
    for param in spec.params:
        if param.kind == "argument":
            raw = values.get(param.name)
            if raw in (None, "", False):
                continue
            args.extend(str(raw).split()) if param.multiple else args.append(str(raw))
            continue
        value = values.get(param.name)
        flag = param.flags[0] if param.flags else f"--{param.name.replace('_', '-')}"
        if param.is_flag:
            if value:
                args.append(flag)
            continue
        if value in (None, ""):
            continue
        if isinstance(value, list):
            for item in value:
                args.extend([flag, str(item)])
        else:
            args.extend([flag, str(value)])
    return args
