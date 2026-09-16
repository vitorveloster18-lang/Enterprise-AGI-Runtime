"""Cognitive Extensions (Fase 3+).

Extensions are Tools or Providers plugged into the Runtime. They never become
core code: everything enters through the Tool Protocol or the Model Gateway.
"""

from ..domain.extension import Extension

__all__ = ["Extension"]
