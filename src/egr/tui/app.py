"""A interface de terminal do EGR (estilo Claude Code / opencode).

É só mais um cliente do Runtime: tudo o que aparece aqui passa pelo mesmo
caminho governado da CLI — política, ferramenta, trilha. Nada de atalho.
"""

from __future__ import annotations

from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import ClassVar

from rich.markup import escape
from rich.panel import Panel
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog, Static
from textual.worker import Worker, WorkerState

from ..cli.main import app as cli_app
from ..runtime.runtime import Runtime
from .catalog import build_catalog, run_command
from .config_form import load_config_dict
from .screens.approvals import ApprovalsScreen
from .screens.help import HelpScreen
from .screens.palette import CommandPalette
from .screens.providers import ProvidersScreen
from .screens.settings import SettingsScreen

# o mesmo critério do `scripts/start.sh`: o que é do ambiente, o que é
# configuração pendente e o que é problema de verdade
EXPECTED_ENVIRONMENT = frozenset({"sandbox:isolamento"})
PENDING_CONFIG = frozenset({"security:chave", "security:identidade"})

BANNER = """\
[bold]Enterprise AGI Runtime[/bold] — interface local, sem servidor.

Escreva o objetivo e Enter: o Runtime cria a task, a política autoriza, a \
ferramenta executa e a trilha registra.
Comece com [b]/[/b] para um comando direto ([b]/task list[/b]) ou [b]ctrl+p[/b] \
para ver todos.

MODEL propõe · RUNTIME governa · TOOL executa · MEMORY lembra · POLICY autoriza · HUMAN aprova.
"""


class EGRApp(App[None]):
    """Painel único: conversa com o Runtime em cima, comandos e status embaixo."""

    TITLE = "EGR — Runtime local"
    # a paleta do próprio textual (ctrl+p) é genérica; a nossa lista os comandos
    # reais do Runtime, com formulário de argumentos
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #status { height: 2; padding: 0 1; background: $boost; color: $text; }
    #log { height: 1fr; border: round $accent; padding: 0 1; }
    #prompt { height: 3; border: round $accent; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+p", "palette", "Comandos"),
        Binding("ctrl+s", "settings", "Configuração"),
        Binding("ctrl+m", "models", "Modelos"),
        Binding("ctrl+a", "approvals", "Aprovações", priority=True),
        Binding("ctrl+d", "doctor", "Doctor", priority=True),
        Binding("ctrl+r", "status", "Status"),
        Binding("ctrl+y", "audit", "Auditoria"),
        Binding("ctrl+t", "selftest", "Auto-teste"),
        Binding("ctrl+l", "clear", "Limpar"),
        Binding("ctrl+h", "help", "Ajuda"),
        Binding("f1", "help", "Ajuda", show=False),
    ]

    def __init__(self, workspace: Path, environment: str | None = None) -> None:
        super().__init__()
        self.workspace = Path(workspace)
        self.environment = environment
        self.runtime = Runtime.load(self.workspace, environment=environment)
        self.data = load_config_dict(self.workspace)
        self.queue: list[list[str]] = []
        self.busy = False

    # ── layout ──────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="status")
        yield RichLog(id="log", markup=True, wrap=True, auto_scroll=True)
        yield Input(
            placeholder="objetivo da task…  (/comando · ctrl+p paleta · ctrl+h ajuda)",
            id="prompt",
        )
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        log.write(Panel(BANNER, title="EGR", border_style="cyan"))
        self.refresh_status()
        self.set_interval(30, self.refresh_status)
        self.query_one("#prompt", Input).focus()

    # ── status ──────────────────────────────────────────────────────────────
    def refresh_status(self) -> None:
        try:
            status = self.runtime.status()
        except Exception as exc:  # nunca derruba a interface por causa do status
            self.query_one("#status", Static).update(
                f"[red]Runtime indisponível:[/red] {escape(str(exc))}"
            )
            return
        counts = status.get("counts", {}) or {}
        tasks = counts.get("tasks", {}) or {}
        pending = counts.get("approvals_pending", 0) or 0
        spend = (status.get("spend", {}) or {}).get("today") or {}
        cost = spend.get("cost", 0) if isinstance(spend, dict) else 0
        events = counts.get("events", 0)
        tasks_txt = ", ".join(f"{name}: {value}" for name, value in list(tasks.items())[:4]) or "nenhuma"
        pending_txt = (
            f"[yellow]{pending} aprovação(ões) pendente(s)[/yellow]" if pending else "sem aprovações"
        )
        line = (
            f"[b]{Path(status.get('workspace', str(self.workspace))).name}[/b]"
            f" · {status.get('environment', '-')}"
            f" · tasks: {escape(tasks_txt)}"
            f" · {pending_txt}"
            f" · modelo: {escape(str(status.get('routing', '-')))}"
            f" · gasto hoje: {cost}"
            f" · trilha: {events} eventos"
        )
        environment, config, real = self._doctor_state()
        if real:
            line += f" · [red]doctor: {len(real)} falha(s) real(is)[/red]"
        elif config:
            line += " · [yellow]doctor: configuração pendente[/yellow]"
        elif environment:
            line += " · [dim]doctor: só pendências do ambiente[/dim]"
        self.query_one("#status", Static).update(line)

    def _doctor_state(self) -> tuple[list[str], list[str], list[str]]:
        """(esperado do ambiente, configuração pendente, problema real)."""

        try:
            items = self.runtime.health().get("checks") or []
        except Exception:
            return [], [], []
        environment: list[str] = []
        config: list[str] = []
        real: list[str] = []
        for item in items:
            if not isinstance(item, dict) or item.get("ok") is not False:
                continue
            name = str(item.get("check", "?"))
            if name in EXPECTED_ENVIRONMENT:
                environment.append(name)
            elif name in PENDING_CONFIG:
                config.append(name)
            else:
                real.append(name)
        return environment, config, real

    # ── execução (sempre pela CLI, nunca por dentro) ────────────────────────
    def run_cli(self, args: list[str], echo: str | None = None) -> None:
        self.queue.append(args)
        if echo:
            self.query_one("#log", RichLog).write(
                Panel(escape(echo), title="comando", border_style="cyan")
            )
        self._pump()

    def _pump(self) -> None:
        if self.busy or not self.queue:
            return
        args = self.queue.pop(0)
        self.busy = True
        self.query_one("#log", RichLog).write(
            Panel(escape("egr " + " ".join(args)), title="executando", border_style="cyan")
        )
        self.run_worker(
            partial(run_command, cli_app, args, str(self.workspace)),
            thread=True,
            group="cli",
            exclusive=False,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "cli" or event.state != WorkerState.SUCCESS:
            return
        self.busy = False
        log = self.query_one("#log", RichLog)
        result = event.worker.result
        if isinstance(result, tuple) and len(result) == 2:
            code, output = result
            style = "green" if code == 0 else ("yellow" if code == 3 else "red")
            title = f"saída (exit {code})"
            log.write(
                Panel(escape((output or "").rstrip()) or "(sem saída)", title=title, border_style=style)
            )
            if code == 3:
                log.write("[yellow]task aguardando aprovação humana — ctrl+a para decidir[/yellow]")
            if code == 1:
                log.write("[red]comando falhou: veja a saída acima[/red]")
        self.refresh_status()
        self._pump()

    # ── entrada ─────────────────────────────────────────────────────────────
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text == "/":
            self.action_palette()
            return
        if text.startswith("/"):
            try:
                import shlex

                args = shlex.split(text[1:])
            except ValueError:
                args = text[1:].split()
            if args:
                self.run_cli(args)
            return
        # linguagem natural: uma task pelo caminho governado
        self.run_cli(["task", "exec", *text.split()], echo=f"task: {text}")

    # ── ações ───────────────────────────────────────────────────────────────
    def action_palette(self) -> None:
        def _done(args: list[str] | None) -> None:
            if args:
                self.run_cli(args)

        self.push_screen(CommandPalette(build_catalog(cli_app)), _done)

    def action_settings(self) -> None:
        def _done(saved: bool | None) -> None:
            if saved:
                self._reload("configuração gravada pela interface")

        self.push_screen(SettingsScreen(self.workspace, self.data), _done)

    def action_models(self) -> None:
        def _done(saved: bool | None) -> None:
            if saved:
                self._reload("provedores gravados pela interface")

        self.push_screen(ProvidersScreen(self.workspace, self.data), _done)

    def action_approvals(self) -> None:
        if len(self.screen_stack) > 1:  # já tem uma tela aberta
            return

        def _done(_: bool | None) -> None:
            self.refresh_status()

        self.push_screen(ApprovalsScreen(self.runtime), _done)

    def action_doctor(self) -> None:
        self.run_cli(["doctor"])

    def action_status(self) -> None:
        self.run_cli(["status"])

    def action_audit(self) -> None:
        self.run_cli(["audit", "verify"])

    def action_selftest(self) -> None:
        self.run_cli(["doctor"])
        self.run_cli(["audit", "verify"])
        self.run_cli(["task", "exec", "verificação", "da", "interface"])

    def action_clear(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # ── apoio ───────────────────────────────────────────────────────────────
    def _reload(self, reason: str) -> None:
        with suppress(Exception):
            self.runtime.audit.record(
                "system.event",
                actor="tui",
                payload={"action": "config.updated", "workspace": str(self.workspace)},
            )
        self.runtime = Runtime.load(self.workspace, environment=self.environment)
        self.data = load_config_dict(self.workspace)
        self.refresh_status()
        self.notify(reason)


def run_tui(workspace: Path, environment: str | None = None) -> None:
    """Abre a interface. Chamada por `egr tui`."""

    EGRApp(Path(workspace), environment=environment).run()


__all__ = ["EGRApp", "run_tui"]
