"""Governance (Fase 9): promoção entre ambientes com versão, evidência e volta.

    development → staging → produção

O caminho é sempre: criar release (snapshot + gates) → submeter → aprovar
(humano) → aplicar. Reverter é restaurar um snapshot — não "desfazer na mão".
"""

from .gates import evaluate
from .manager import ReleaseManager
from .versions import VersionStore

__all__ = ["ReleaseManager", "VersionStore", "evaluate"]
