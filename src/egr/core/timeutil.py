"""Time helpers. Everything is stored and compared in UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    """ISO-8601 UTC com microssegundos.

    Precisão importa: colunas de ordenação (`ORDER BY created_at DESC`) com
    truncagem em segundos empatam execuções feitas no mesmo segundo, e o
    "último" registro passa a depender da ordem física da tabela.
    """

    dt = dt or utcnow()
    return dt.isoformat(timespec="microseconds")


def parse(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
