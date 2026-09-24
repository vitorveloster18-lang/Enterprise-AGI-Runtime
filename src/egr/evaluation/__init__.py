"""Evaluation (Fase 8): provar que o trabalho é bom — com números.

    suíte → casos medidos → métricas → baseline → veredito

Segurança (Fase 7) responde "pode entrar?". Avaliação responde "continua bom?".
"""

from .loader import load_suite_dir, load_suite_file
from .metrics import aggregate, compare, verdict
from .runner import EvaluationRunner
from .security import scan
from .suites import smoke_suite

__all__ = [
    "EvaluationRunner",
    "aggregate",
    "compare",
    "load_suite_dir",
    "load_suite_file",
    "scan",
    "smoke_suite",
    "verdict",
]
