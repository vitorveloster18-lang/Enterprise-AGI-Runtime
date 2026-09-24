"""Lacuna 6b: gatilhos de banco — o dado muda, o Runtime fica sabendo.

A Fase 6 dispara workflow por evento do Runtime, por cron e por webhook. Faltava
o caso mais comum no mundo real: **uma linha mudou no banco** (uma task travou,
uma aprovação expirou, uma avaliação reprovou).

Como funciona:

1. o gatilho é declarado (`tabela`, `evento`, `quando`, `emite`);
2. o Runtime cria um `CREATE TRIGGER` de verdade no SQLite — quem avisa é o
   banco, não um processo olhando tabela de negócio de minuto em minuto;
3. o aviso cai em `db_events` (fila com `processed_at`);
4. `drain()` lê o que não foi processado, emite o evento no ledger (o mesmo
   EventBus dos triggers por evento) e marca como processado.

Segurança: `quando` **não é SQL livre**. Só entram colunas da lista branca da
tabela, `NEW.`/`OLD.` conforme o evento, comparações e operadores lógicos.
Qualquer outra coisa é recusada antes de chegar no banco.
"""

from __future__ import annotations

import re
from typing import Any

from ..core.errors import ConfigError
from ..domain.coordination import DatabaseEvent, DatabaseTrigger
from ..domain.enums import EventType

#: tabela -> colunas que podem aparecer no payload e no `when`
ALLOWED_TABLES: dict[str, tuple[str, ...]] = {
    "tasks": ("id", "status", "agent_id", "environment"),
    "approvals": ("id", "status", "task_id", "environment"),
    "evaluation_runs": ("id", "suite_id", "status", "target"),
    "integration_jobs": ("id", "integration", "status", "attempts"),
}
EVENTS = ("insert", "update", "delete")
#: referências permitidas por evento (SQLite não tem OLD em INSERT, nem NEW em DELETE)
REFERENCES = {"insert": ("NEW",), "update": ("NEW", "OLD"), "delete": ("OLD",)}
WORDS = {"AND", "OR", "NOT", "IS", "NULL", "LIKE", "IN", "TRUE", "FALSE"}
NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
      | (?P<num>\d+(?:\.\d+)?)
      | (?P<str>'(?:[^']|'')*')
      | (?P<op><>|!=|<=|>=|=|<|>|\(|\)|,)
    )""",
    re.VERBOSE,
)


def validate(trigger: DatabaseTrigger) -> None:
    """Recusa qualquer coisa que não seja comparação sobre colunas liberadas."""

    if trigger.table not in ALLOWED_TABLES:
        raise ConfigError(
            f"tabela '{trigger.table}' fora da lista branca ({', '.join(sorted(ALLOWED_TABLES))})"
        )
    if trigger.event not in EVENTS:
        raise ConfigError(f"evento '{trigger.event}' inválido (use {', '.join(EVENTS)})")
    if not trigger.emit or not NAME.match(trigger.emit):
        raise ConfigError(f"evento emitido inválido: '{trigger.emit}' (use minúsculas e pontos)")
    if not NAME.match(trigger.name.replace(" ", "_")) and not trigger.name:
        raise ConfigError("gatilho sem nome")
    validate_expression(trigger.when, trigger.table, trigger.event)


def validate_expression(expression: str, table: str, event: str) -> None:
    """A expressão do `WHEN`: só colunas liberadas, literais e comparações."""

    if not expression.strip():
        return
    allowed = set(ALLOWED_TABLES.get(table, ()))
    references = REFERENCES[event]
    position = 0
    length = len(expression)
    while position < length:
        match = _TOKEN.match(expression, position)
        if match is None:
            raise ConfigError(
                f"expressão recusada: trecho não reconhecido em "
                f"'{expression[position:position + 24].strip()}'"
            )
        position = match.end()
        word = match.group("word")
        if not word:
            continue
        if word.upper() in WORDS:
            continue
        if "." in word:
            prefix, _, column = word.partition(".")
            if prefix.upper() not in references:
                raise ConfigError(
                    f"'{prefix}' não existe em gatilho de {event} (use {' ou '.join(references)})"
                )
            if column not in allowed:
                raise ConfigError(
                    f"coluna '{column}' fora da lista branca de {table} "
                    f"({', '.join(sorted(allowed))})"
                )
            continue
        raise ConfigError(f"expressão recusada: '{word}' não é coluna nem operador permitido")


class TriggerManager:
    """Instala, lista e drena gatilhos de banco."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    # ---- ciclo --------------------------------------------------------
    def install(self, trigger: DatabaseTrigger, *, actor: str = "cli") -> DatabaseTrigger:
        validate(trigger)
        columns = ALLOWED_TABLES[trigger.table]
        reference = "NEW" if trigger.event in ("insert", "update") else "OLD"
        payload = ", ".join(f"'{column}', {reference}.{column}" for column in columns)
        when = f"WHEN ({trigger.when})" if trigger.when.strip() else ""
        sql = (
            f"CREATE TRIGGER IF NOT EXISTS {trigger.trigger_name} "
            f"AFTER {trigger.event.upper()} ON {trigger.table} FOR EACH ROW {when} "
            f"BEGIN INSERT INTO db_events (id, trigger_id, event, table_name, row_id, payload, created_at) "
            f"VALUES ('evt_' || lower(hex(randomblob(8))), '{trigger.id}', '{trigger.emit}', "
            f"'{trigger.table}', {reference}.id, json_object({payload}), datetime('now')); END;"
        )
        self.runtime.db.executescript(sql)
        saved = self.runtime.db_triggers.save(trigger)
        self.runtime.audit.record(
            EventType.DB_TRIGGER_INSTALLED,
            actor=actor,
            environment=str(self.runtime.settings.config.environment),
            payload={
                "gatilho": saved.id,
                "nome": saved.name,
                "tabela": saved.table,
                "evento": saved.event,
                "quando": saved.when or "sempre",
                "emite": saved.emit,
            },
        )
        return saved

    def uninstall(self, trigger_id: str, *, actor: str = "cli") -> None:
        trigger = self.runtime.db_triggers.get(trigger_id)
        if trigger is None:
            raise ConfigError(f"gatilho não encontrado: {trigger_id}")
        self.runtime.db.execute(f"DROP TRIGGER IF EXISTS {trigger.trigger_name}")
        self.runtime.db.execute("DELETE FROM db_events WHERE trigger_id = ? AND processed_at IS NULL", (trigger_id,))
        self.runtime.db.commit()
        self.runtime.db_triggers.delete(trigger_id)

    def install_all(self, *, actor: str = "runtime") -> int:
        """Reinstala os gatilhos ativos (idempotente: `IF NOT EXISTS`)."""

        installed = 0
        for trigger in self.runtime.db_triggers.list(enabled_only=True):
            try:
                self.install(trigger, actor=actor)
                installed += 1
            except ConfigError:
                continue  # definição inválida não derruba o boot do Runtime
        return installed

    def list(self) -> list[DatabaseTrigger]:
        return self.runtime.db_triggers.list()

    # ---- dreno --------------------------------------------------------
    def drain(self, limit: int = 50, *, actor: str = "db_trigger") -> list[DatabaseEvent]:
        """Transforma o que o banco avisou em evento do Runtime, na ordem."""

        pending = self.runtime.db_events.pending(limit=limit)
        for event in pending:
            # publica no barramento (e por ele no ledger): é o mesmo caminho de
            # qualquer evento do Runtime — e por isso dispara workflow por gatilho
            self.runtime.events.publish(
                event.event,
                actor=actor,
                environment=str(self.runtime.settings.config.environment),
                payload={
                    "gatilho": event.trigger_id,
                    "tabela": event.table,
                    "linha": event.row_id,
                    "dados": event.payload,
                },
            )
            self.runtime.db_events.mark_processed(event.id)
            self.runtime.audit.record(
                EventType.DB_TRIGGER_FIRED,
                actor=actor,
                environment=str(self.runtime.settings.config.environment),
                payload={"evento": event.id, "emite": event.event, "tabela": event.table},
            )
        return pending

    def events(self, limit: int = 20) -> list[DatabaseEvent]:
        return self.runtime.db_events.list(limit=limit)

    def status(self) -> dict[str, Any]:
        return {
            "gatilhos": [item.summary() for item in self.list()],
            "fila": self.runtime.db_events.count(pending_only=True),
            "processados": self.runtime.db_events.count(),
            "tabelas_permitidas": sorted(ALLOWED_TABLES),
        }


__all__ = ["ALLOWED_TABLES", "EVENTS", "TriggerManager", "validate", "validate_expression"]
