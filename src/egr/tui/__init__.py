"""Interface de terminal do EGR (estilo Claude Code / opencode).

É um cliente do Runtime como a CLI e a API: nada aqui executa por conta própria,
tudo passa pelo caminho governado. Requer o extra opcional `.[tui]`.
"""

from __future__ import annotations

from .app import EGRApp, run_tui

__all__ = ["EGRApp", "run_tui"]
