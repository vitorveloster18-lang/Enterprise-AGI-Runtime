"""Cadastro de provedores de modelo (de onde o Runtime pode chamar IA)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

from ..config_form import PROVIDER_FIELDS, ProviderField, providers_of, read_value, save_config_dict, set_providers

# `api_key_env` guarda o NOME da variável (GOOGLE_API_KEY), nunca a chave: um
# nome fora desse formato é erro de preenchimento — e é recusado no formulário
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def valid_env_name(value: str) -> bool:
    """Diz se o texto serve como nome de variável de ambiente."""

    return bool(_ENV_NAME.match(value))


def blank_provider() -> dict[str, Any]:
    return {
        "name": "novo-provedor",
        "type": "openai_compat",
        "enabled": True,
        "model": None,
        "base_url": None,
        "api_key_env": None,
        "external": False,
        "priority": 10,
        "capabilities": ["reasoning", "chat"],
        "pricing": {"currency": "USD", "input_per_1m": 0.0, "output_per_1m": 0.0, "per_call": 0.0},
    }


class ProviderForm(ModalScreen[dict[str, Any] | None]):
    """Um provedor: tipo, URL, modelo e o NOME da variável com a chave."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "cancel", "Cancelar")]

    CSS = """
    ProviderForm { align: center middle; }
    ProviderForm > Vertical { width: 88%; height: 88%; background: $panel; border: round $accent; padding: 1 2; }
    ProviderForm .row { height: auto; }
    ProviderForm .row Label { width: 30; }
    ProviderForm .row Input { width: 1fr; }
    ProviderForm .help { color: $text-muted; height: auto; padding: 0 0 0 2; }
    ProviderForm .buttons { height: auto; padding: 1 0 0 0; }
    """

    def __init__(self, provider: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.provider = dict(provider) if provider else blank_provider()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Provedor de modelo", classes="title")
            with VerticalScroll():
                for field in PROVIDER_FIELDS:
                    with Horizontal(classes="row"):
                        yield Label(field.label)
                        yield self._widget(field)
                    if field.help:
                        yield Static(field.help, classes="help")
            with Horizontal(classes="buttons"):
                yield Button("Salvar", variant="success", id="save")
                yield Button("Cancelar", id="cancel")

    def _widget(self, field: ProviderField):
        current = read_value(self.provider, field.key)
        if field.kind == "bool":
            return Checkbox("sim", value=bool(current), id=f"f_{field.key.replace('.', '_')}")
        if field.kind == "choice":
            options = [(choice, choice) for choice in field.choices]
            return Select(options, value=current if current in field.choices else field.choices[0],
                          allow_blank=False, id=f"f_{field.key.replace('.', '_')}")
        if field.kind == "int":
            return Input(value="" if current is None else str(current), id=f"f_{field.key.replace('.', '_')}")
        if field.kind == "float":
            return Input(value="" if current is None else str(current), id=f"f_{field.key.replace('.', '_')}")
        if field.key == "capabilities":
            value = current if isinstance(current, list) else []
            return Input(value=", ".join(str(item) for item in value), id=f"f_{field.key.replace('.', '_')}")
        return Input(value="" if current is None else str(current), id=f"f_{field.key.replace('.', '_')}")

    def _collect(self) -> dict[str, Any] | None:
        data = dict(self.provider)
        for field in PROVIDER_FIELDS:
            widget = self.query_one(f"#f_{field.key.replace('.', '_')}")
            raw = widget.value  # type: ignore[union-attr]
            try:
                if field.kind == "bool":
                    value: Any = bool(raw)
                elif field.kind == "choice":
                    value = str(raw)
                elif field.kind == "int":
                    text = str(raw).strip()
                    value = int(text) if text else 0
                elif field.kind == "float":
                    text = str(raw).strip()
                    value = float(text) if text else 0.0
                elif field.key == "capabilities":
                    value = [item.strip() for item in str(raw).split(",") if item.strip()]
                else:
                    text = str(raw).strip()
                    value = text or None
                    if field.key == "name" and not value:
                        self.notify("dê um nome ao provedor (ex.: google)", severity="error")
                        return None
                    if field.key == "api_key_env" and value and not valid_env_name(value):
                        self.notify(
                            "Chave: use só o NOME da variável (ex.: GOOGLE_API_KEY)",
                            severity="error",
                        )
                        return None
            except ValueError as exc:
                self.notify(f"{field.label}: {exc}", severity="error")
                return None
            parts = field.key.split(".")
            node = data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        return data

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        data = self._collect()
        if data is not None:
            self.dismiss(data)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProvidersScreen(ModalScreen[bool]):
    """Lista de provedores: adicionar, editar, remover e gravar."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Fechar"),
        Binding("ctrl+s", "save", "Gravar", priority=True),
        Binding("a", "add", "Adicionar"),
        Binding("r", "remove", "Remover"),
    ]

    CSS = """
    ProvidersScreen { align: center middle; }
    ProvidersScreen > Vertical { width: 92%; height: 88%; background: $panel; border: round $accent; padding: 1 2; }
    ProvidersScreen DataTable { height: 1fr; }
    ProvidersScreen .buttons { height: auto; padding: 1 0 0 0; }
    ProvidersScreen .help { color: $text-muted; height: auto; }
    """

    def __init__(self, workspace: Path, data: dict[str, Any]) -> None:
        super().__init__()
        self.workspace = workspace
        self.data = data
        self.providers = providers_of(data)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Provedores — a chave vai como nome de variável de ambiente, nunca no arquivo")
            yield DataTable(id="table")
            yield Static("Enter edita · a adiciona · r remove · ctrl+s grava", classes="help")
            with Horizontal(classes="buttons"):
                yield Button("Adicionar", variant="success", id="add")
                yield Button("Editar", id="edit")
                yield Button("Remover", variant="error", id="remove")
                yield Button("Gravar", id="save")
                yield Button("Fechar", id="close")

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("nome", "tipo", "modelo", "ativo", "externo", "prioridade")
        self._refresh()

    def _refresh(self) -> None:
        # a chave da linha é o índice, nunca o nome: dois provedores com o
        # mesmo nome não podem mais derrubar a tabela com DuplicateKey — e a
        # seleção já é posicional (cursor_row), então nada mais muda
        table = self.query_one("#table", DataTable)
        table.clear()
        for index, provider in enumerate(self.providers):
            table.add_row(
                str(provider.get("name", "-")),
                str(provider.get("type", "-")),
                str(provider.get("model") or "-"),
                "sim" if provider.get("enabled", True) else "não",
                "sim" if provider.get("external") else "não",
                str(provider.get("priority", 0)),
                key=f"provider-{index}",
            )

    def _selected(self) -> int | None:
        table = self.query_one("#table", DataTable)
        if table.cursor_row is None:
            return None
        index = table.cursor_row
        return index if 0 <= index < len(self.providers) else None

    def _duplicate_name(self, provider: dict[str, Any], ignore: int = -1) -> bool:
        name = (provider.get("name") or "").strip().lower()
        if not name:
            return False
        return any(
            position != ignore and (item.get("name") or "").strip().lower() == name
            for position, item in enumerate(self.providers)
        )

    def _remove_selected(self) -> None:
        index = self._selected()
        if index is None:
            return
        name = self.providers[index].get("name", "?")
        self.providers.pop(index)
        self._refresh()
        self.notify(f"{name} removido (grave para confirmar)")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "add":
            self.action_add()
        elif action == "edit":
            index = self._selected()
            if index is None:
                self.notify("escolha um provedor", severity="warning")
                return
            self.app.push_screen(ProviderForm(self.providers[index]), self._edited(index))
        elif action == "remove":
            self._remove_selected()
        elif action == "save":
            self.action_save()
        else:
            self.dismiss(False)

    def action_add(self) -> None:
        self.app.push_screen(ProviderForm(), self._added)

    def action_remove(self) -> None:
        self._remove_selected()

    def _added(self, provider: dict[str, Any] | None) -> None:
        if not provider:
            return
        if self._duplicate_name(provider):
            self.notify(f"já existe um provedor '{provider.get('name')}'", severity="error", timeout=6)
            self.app.push_screen(ProviderForm(provider), self._added)
            return
        self.providers.append(provider)
        self._refresh()

    def _edited(self, index: int):
        def _done(provider: dict[str, Any] | None) -> None:
            if not provider:
                return
            if self._duplicate_name(provider, ignore=index):
                self.notify(
                    f"já existe um provedor '{provider.get('name')}'", severity="error", timeout=6
                )
                self.app.push_screen(ProviderForm(provider), self._edited(index))
                return
            self.providers[index] = provider
            self._refresh()

        return _done

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        index = self._selected()
        if index is not None:
            self.app.push_screen(ProviderForm(self.providers[index]), self._edited(index))

    def action_save(self) -> None:
        set_providers(self.data, self.providers)
        try:
            save_config_dict(self.workspace, self.data)
        except Exception as exc:
            self.notify(f"configuração recusada: {exc}", severity="error", timeout=8)
            return
        self.notify("provedores gravados", severity="information")
        self.dismiss(True)

    def action_close(self) -> None:
        self.dismiss(False)
