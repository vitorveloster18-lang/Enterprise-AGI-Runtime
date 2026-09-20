"""Paleta de comandos e formulário de argumentos."""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, OptionList, Static

from ..catalog import CommandSpec, build_args


class ArgsForm(ModalScreen[list[str] | None]):
    """Monta a linha de comando de um comando sem exigir que ninguém decore flag."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "cancel", "Cancelar")]

    CSS = """
    ArgsForm { align: center middle; }
    ArgsForm > Vertical { width: 90%; height: 88%; background: $panel; border: round $accent; padding: 1 2; }
    ArgsForm .row { height: auto; }
    ArgsForm .row Label { width: 28; }
    ArgsForm .row Input { width: 1fr; }
    ArgsForm .help { color: $text-muted; height: auto; padding: 0 0 0 2; }
    ArgsForm .title { padding: 0 0 1 0; text-style: bold; }
    ArgsForm .buttons { height: auto; padding: 1 0 0 0; }
    ArgsForm VerticalScroll { height: 1fr; }
    """

    def __init__(self, spec: CommandSpec) -> None:
        super().__init__()
        self.spec = spec

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"egr {self.spec.path}", classes="title")
            if self.spec.help:
                yield Static(self.spec.help, classes="help")
            with VerticalScroll():
                if not self.spec.params:
                    yield Static("Este comando não recebe parâmetros.", classes="help")
                for param in self.spec.params:
                    if param.kind == "argument":
                        label = f"{param.name}{' *' if param.required else ''}"
                    else:
                        label = f"{' '.join(param.flags)}{' *' if param.required else ''}"
                    with Horizontal(classes="row"):
                        yield Label(label)
                        if param.is_flag:
                            yield Checkbox("ativar", value=bool(param.default), id=f"p_{param.name}")
                        else:
                            default = "" if param.default is None else str(param.default)
                            yield Input(value=default, placeholder=param.help or "", id=f"p_{param.name}")
                    if param.help:
                        yield Static(param.help, classes="help")
            with Horizontal(classes="buttons"):
                yield Button("Executar", variant="success", id="run")
                yield Button("Cancelar", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        values: dict[str, Any] = {}
        for param in self.spec.params:
            widget = self.query_one(f"#p_{param.name}")
            if isinstance(widget, Checkbox):
                values[param.name] = widget.value
            else:
                values[param.name] = widget.value  # type: ignore[union-attr]
        try:
            self.dismiss(build_args(self.spec, values))
        except Exception as exc:  # mensagem de erro sem derrubar a interface
            self.notify(str(exc), severity="error")

    def action_cancel(self) -> None:
        self.dismiss(None)


class CommandPalette(ModalScreen[list[str] | None]):
    """Todos os comandos da CLI, com busca."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "cancel", "Fechar")]

    CSS = """
    CommandPalette { align: center middle; }
    CommandPalette > Vertical { width: 90%; height: 80%; background: $panel; border: round $accent; padding: 1 2; }
    CommandPalette #query { margin: 0 0 1 0; }
    CommandPalette OptionList { height: 1fr; }
    CommandPalette .help { color: $text-muted; height: auto; }
    """

    def __init__(self, specs: list[CommandSpec]) -> None:
        super().__init__()
        self.specs = specs
        self.shown: list[CommandSpec] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(placeholder="buscar comando (ex.: memory, policy, release)…", id="query")
            yield OptionList(id="options")
            yield Static("↑↓ navega · Enter escolhe · Esc fecha", classes="help")

    def on_mount(self) -> None:
        self._fill("")
        self.query_one("#query", Input).focus()

    def _fill(self, term: str) -> None:
        needle = term.strip().lower()
        self.shown = [
            spec
            for spec in self.specs
            if not needle
            or needle in spec.path.lower()
            or needle in spec.help.lower()
            or needle in spec.group.lower()
        ]
        options = self.query_one("#options", OptionList)
        options.clear_options()
        for spec in self.shown[:400]:
            help_text = f" — {spec.help}" if spec.help else ""
            options.add_option(f"egr {spec.path}{help_text}")
        if self.shown:
            options.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self._fill(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        options = self.query_one("#options", OptionList)
        index = options.highlighted if options.highlighted is not None else 0
        self._choose(index)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._choose(event.option_index)

    def _choose(self, index: int | None) -> None:
        if index is None or index >= len(self.shown):
            return
        spec = self.shown[index]
        if not spec.params:
            self.dismiss(list(spec.path.split(" ")))
            return

        def _done(args: list[str] | None) -> None:
            self.dismiss(args)

        self.app.push_screen(ArgsForm(spec), _done)

    def action_cancel(self) -> None:
        self.dismiss(None)
