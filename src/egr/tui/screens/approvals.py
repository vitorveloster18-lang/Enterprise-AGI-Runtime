"""Aprovações pendentes: o humano decide aqui, com o contexto na tela."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

DECISIONS = {"approve": "aprovar", "deny": "negar"}


class ApprovalsScreen(ModalScreen[bool]):
    """Nenhuma aprovação é automática: a decisão passa por esta tela."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "close", "Fechar"),
        ("a", "approve", "Aprovar"),
        ("d", "deny", "Negar"),
        ("r", "refresh", "Atualizar"),
    ]

    CSS = """
    ApprovalsScreen { align: center middle; }
    ApprovalsScreen > Vertical { width: 92%; height: 80%; background: $panel; border: round $accent; padding: 1 2; }
    ApprovalsScreen DataTable { height: 1fr; }
    ApprovalsScreen .buttons { height: auto; padding: 1 0 0 0; }
    ApprovalsScreen .help { color: $text-muted; height: auto; }
    """

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Aprovações pendentes — decidir aqui é o HUMANO aprovando, não o modelo")
            yield DataTable(id="table")
            yield Static("a aprova · d nega · r atualiza · Esc fecha", classes="help")
            with Horizontal(classes="buttons"):
                yield Button("Aprovar", variant="success", id="approve")
                yield Button("Negar", variant="error", id="deny")
                yield Button("Atualizar", id="refresh")
                yield Button("Fechar", id="close")

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("id", "ação", "solicitado por", "papel exigido", "task", "quando")
        self._refresh()

    def _pending(self):
        try:
            return self.runtime.approvals.list(status="pending", limit=100)
        except Exception as exc:  # banco indisponível não derruba a interface
            self.notify(f"não consegui ler as aprovações: {exc}", severity="error")
            return []

    def _refresh(self) -> None:
        table = self.query_one("#table", DataTable)
        table.clear()
        self.rows = self._pending()
        for approval in self.rows:
            table.add_row(
                approval.id,
                approval.tool,
                approval.requested_by,
                approval.required_role or "-",
                approval.task_id or "-",
                approval.created_at.strftime("%d/%m %H:%M"),
            )

    def _selected_id(self) -> str | None:
        table = self.query_one("#table", DataTable)
        row = table.cursor_row
        if row is None or not getattr(self, "rows", None):
            return None
        return self.rows[row].id if 0 <= row < len(self.rows) else None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "refresh":
            self._refresh()
        elif action == "close":
            self.dismiss(False)
        elif action in DECISIONS:
            self._decide(action)

    def _decide(self, decision: str) -> None:
        approval_id = self._selected_id()
        if not approval_id:
            self.notify("escolha uma aprovação", severity="warning")
            return
        try:
            if decision == "approve":
                task = self.runtime.approve(approval_id, decided_by="human")
                detail = f" — task {task.id} → {task.status}" if task is not None else ""
                self.notify(f"aprovado{detail}", severity="information")
            else:
                self.runtime.deny(approval_id, decided_by="human", note="negado pela interface")
                self.notify("negado", severity="warning")
        except Exception as exc:
            self.notify(f"não foi possível decidir: {exc}", severity="error", timeout=8)
        self._refresh()
        self.dismiss(True)

    def action_approve(self) -> None:
        self._decide("approve")

    def action_deny(self) -> None:
        self._decide("deny")

    def action_refresh(self) -> None:
        self._refresh()

    def action_close(self) -> None:
        self.dismiss(False)
