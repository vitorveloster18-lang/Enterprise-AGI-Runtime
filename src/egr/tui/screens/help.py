"""Ajuda: atalhos e o que cada coisa significa."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

HELP = """\
[bold]Atalhos[/bold]

  ctrl+p   paleta de comandos (todos os comandos da CLI)
  ctrl+s   configuração do workspace
  ctrl+m   provedores de modelo (onde vive a chave de API)
  ctrl+a   aprovações pendentes
  ctrl+d   doctor (saúde do Runtime)
  ctrl+r   status
  ctrl+y   trilha de auditoria (verify)
  ctrl+l   limpar a tela
  ctrl+t   repetir o auto-teste
  ctrl+h   esta ajuda (f1 também)
  ctrl+c   sair

[bold]Como falar com o Runtime[/bold]

  · escreva o objetivo em linguagem natural e Enter:
    o Runtime cria a task, passa pela política, executa as ferramentas
    e registra tudo na trilha auditada;
  · comece com / para um comando direto: /task list, /memory search "contrato";
  · / sozinho abre a paleta com todos os comandos.

[bold]As seis regras que nada aqui contorna[/bold]

  MODEL propõe · RUNTIME governa · TOOL executa · MEMORY lembra
  POLICY autoriza · HUMAN aprova o crítico.

  Promoção para produção nunca é automática, e nenhum pack, canal,
  integração ou fila é atalho de promoção.
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Fechar"),
        Binding("f1", "close", "Fechar", priority=True),
    ]

    CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > Vertical { width: 90%; height: 88%; background: $panel; border: round $accent; padding: 1 2; }
    HelpScreen VerticalScroll { height: 1fr; }
    HelpScreen .foot { color: $text-muted; padding: 1 0 0 0; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            with VerticalScroll():
                yield Static(HELP)
            yield Static("Esc ou f1 fecha", classes="foot")

    def action_close(self) -> None:
        self.dismiss(None)
