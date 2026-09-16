"""Cron de 5 campos, sem dependências.

    minuto hora dia_do_mes mes dia_da_semana
      0-59  0-23   1-31   1-12    0-6 (0 = domingo)

Sintaxe suportada: `*`, `*/n`, `a`, `a-b`, `a-b/n`, e listas `a,b,c`.

O scheduler do EGR **não é um daemon mágico**: quem decide quando ele roda é o
operador (systemd, cron do SO, k8s) chamando `egr workflow tick`. Este módulo só
responde "esta expressão está vencida agora?".
"""

from __future__ import annotations

from datetime import datetime, timedelta

FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
FIELD_NAMES = ("minuto", "hora", "dia do mês", "mês", "dia da semana")


class CronError(ValueError):
    pass


def _parse_field(value: str, minimum: int, maximum: int, name: str) -> set[int]:
    value = (value or "").strip()
    if not value:
        raise CronError(f"campo '{name}' vazio")

    allowed: set[int] = set()
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise CronError(f"campo '{name}' com vírgula solta")
        step = 1
        if "/" in chunk:
            chunk, _, step_text = chunk.partition("/")
            if not step_text.isdigit() or int(step_text) <= 0:
                raise CronError(f"passo inválido em '{name}': {step_text}")
            step = int(step_text)
            chunk = chunk.strip() or "*"
        if chunk == "*":
            start, end = minimum, maximum
        elif "-" in chunk:
            start_text, _, end_text = chunk.partition("-")
            if not (start_text.isdigit() and end_text.isdigit()):
                raise CronError(f"intervalo inválido em '{name}': {chunk}")
            start, end = int(start_text), int(end_text)
        elif chunk.isdigit():
            start = end = int(chunk)
        else:
            raise CronError(f"valor inválido em '{name}': {chunk}")

        if start < minimum or end > maximum or start > end:
            raise CronError(f"intervalo fora da faixa em '{name}': {chunk}")
        allowed.update(range(start, end + 1, step))
    return allowed


class CronExpression:
    def __init__(self, expression: str):
        self.expression = (expression or "").strip()
        fields = self.expression.split()
        if len(fields) != 5:
            raise CronError(
                f"expressão cron inválida: '{expression}' "
                "(esperado: minuto hora dia_do_mes mes dia_da_semana)"
            )
        self.fields = [
            _parse_field(text, minimum, maximum, name)
            for text, (minimum, maximum), name in zip(fields, FIELD_RANGES, FIELD_NAMES, strict=True)
        ]

    def matches(self, moment: datetime) -> bool:
        values = (moment.minute, moment.hour, moment.day, moment.month, (moment.weekday() + 1) % 7)
        return all(value in field for value, field in zip(values, self.fields, strict=True))

    def next_after(self, moment: datetime, *, horizon_days: int = 366) -> datetime | None:
        """Próximo disparo, por varredura de minutos (expressões curtas e locais)."""

        candidate = (moment.replace(second=0, microsecond=0) + timedelta(minutes=1))
        limit = moment + timedelta(days=horizon_days)
        while candidate <= limit:
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        return None

    def __str__(self) -> str:
        return self.expression


def is_valid(expression: str) -> bool:
    try:
        CronExpression(expression)
    except CronError:
        return False
    return True


__all__ = ["CronError", "CronExpression", "is_valid"]
