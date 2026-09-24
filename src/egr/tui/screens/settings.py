"""Tela de configuração: tudo o que dá para mudar sem abrir código."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from ..config_form import (
    FieldSpec,
    coerce,
    display,
    fields_of,
    read_value,
    save_config_dict,
    sections,
    write_value,
)


class FieldRow(Horizontal):
    """Uma linha: rótulo + controle, com a ajuda embaixo."""

    def __init__(self, spec: FieldSpec, value: Any) -> None:
        super().__init__(classes="field")
        self.spec = spec
        self.value = value

    def compose(self) -> ComposeResult:
        yield Label(self.spec.label, classes="flabel")
        if self.spec.kind == "bool":
            yield Switch(value=bool(self.value), id="control")
        elif self.spec.kind == "choice":
            options = [(choice, choice) for choice in self.spec.choices]
            current = self.value if self.value in self.spec.choices else self.spec.choices[0]
            yield Select(options, value=current, allow_blank=False, id="control")
        else:
            yield Input(value=display(self.spec, self.value), id="control")

    @property
    def raw(self) -> Any:
        control = self.query_one("#control")
        if isinstance(control, Switch):
            return control.value
        if isinstance(control, Select):
            return control.value
        return control.value  # type: ignore[union-attr]


class SettingsScreen(ModalScreen[bool]):
    """Editor do `egr.yaml` com validação antes de gravar."""

    # priority=True: sem isso o atalho da app (que também usa ctrl+s) atropela
    # o da tela e abre outra tela em vez de salvar
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancelar"),
        Binding("ctrl+s", "save", "Salvar", priority=True),
        Binding("ctrl+m", "providers", "Modelos", priority=True),
    ]

    CSS = """
    SettingsScreen { align: center middle; }
    SettingsScreen > Vertical { width: 95%; height: 92%; background: $panel; border: round $accent; padding: 1 2; }
    SettingsScreen .section { padding: 1 0 0 0; text-style: bold; color: $accent; }
    SettingsScreen .field { height: auto; }
    SettingsScreen .flabel { width: 30; }
    SettingsScreen .field Input { width: 1fr; }
    SettingsScreen .field Select { width: 30; }
    SettingsScreen .help { color: $text-muted; height: auto; padding: 0 0 0 2; width: 100%; }
    SettingsScreen VerticalScroll { height: 1fr; }
    SettingsScreen .buttons { height: auto; padding: 1 0 0 0; }
    """

    def __init__(self, workspace: Path, data: dict[str, Any]) -> None:
        super().__init__()
        self.workspace = workspace
        self.data = data

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Configuração do workspace — segredo nunca é gravado aqui (use nome de variável)")
            with VerticalScroll():
                for section in sections():
                    yield Static(section, classes="section")
                    for spec in fields_of(section):
                        yield FieldRow(spec, read_value(self.data, spec.path))
                        if spec.help:
                            yield Static(spec.help, classes="help")
            with Horizontal(classes="buttons"):
                yield Button("Salvar", variant="success", id="save")
                yield Button("Modelos (provedores)", id="providers")
                yield Button("Cancelar", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        elif event.button.id == "providers":
            self.action_providers()
        else:
            self.dismiss(False)

    def _collect(self) -> bool:
        for row in self.query(FieldRow):
            spec = row.spec
            try:
                write_value(self.data, spec.path, coerce(spec, row.raw))
            except (TypeError, ValueError) as exc:
                self.notify(f"{spec.label}: {exc}", severity="error")
                row.query_one("#control").focus()
                return False
        return True

    def action_save(self) -> None:
        if not self._collect():
            return
        try:
            save_config_dict(self.workspace, self.data)
        except Exception as exc:  # pydantic manda a mensagem exata do campo inválido
            self.notify(f"configuração recusada: {exc}", severity="error", timeout=8)
            return
        self.notify("configuração gravada", severity="information")
        self.dismiss(True)

    def action_providers(self) -> None:
        from .providers import ProvidersScreen

        self._collect()
        self.app.push_screen(ProvidersScreen(self.workspace, self.data))

    def action_cancel(self) -> None:
        self.dismiss(False)
