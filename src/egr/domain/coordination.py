"""Lacuna 6b: coordenação negociada e gatilhos de banco.

Duas coisas que a Fase 6 deixou de fora:

1. **quem executa** era escolha do humano (ou do primeiro agente da lista).
   Aqui os agentes *declaram* um lance e o Runtime escolhe por uma estratégia
   declarada — com o motivo registrado;
2. **o que dispara um workflow** era evento do Runtime, cron ou webhook. Faltava
   o caso mais comum no mundo real: **alguém mexeu no banco**.

Nada disso é decisão automática escondida: negociação e handoff são registrados,
e o gatilho de banco só existe dentro de uma lista branca de tabelas e colunas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..core.timeutil import utcnow

#: estratégias de escolha — todas determinísticas (empate desempata por nome)
STRATEGIES = ("equilibrado", "menor_custo", "menor_fila", "declarado")


class Bid(BaseModel):
    """O lance de um agente: quanto custa, qual a fila e por que ele pode.

    Lance não é opinião do modelo: é número declarado (custo estimado pelo
    provedor que o agente usa e tamanho da fila dele). Quem não pode executar
    aparece com `allowed=False` e o motivo do veto — recusa visível.
    """

    agent: str
    capability: str = ""
    estimated_cost: float = 0.0
    queue: int = 0
    score: float = 0.0
    allowed: bool = True
    veto: str = ""
    reason: str = ""

    @property
    def label(self) -> str:
        if not self.allowed:
            return f"{self.agent}: vetado ({self.veto})"
        return f"{self.agent}: nota {self.score:.4f} · custo {self.estimated_cost:.6f} · fila {self.queue}"

    def summary(self) -> dict[str, Any]:
        return {
            "agente": self.agent,
            "capacidade": self.capability,
            "custo_estimado": round(self.estimated_cost, 6),
            "fila": self.queue,
            "nota": round(self.score, 6),
            "pode": self.allowed,
            "veto": self.veto or "-",
            "motivo": self.reason or "-",
        }


class Negotiation(BaseModel):
    """Uma rodada de negociação: quem disputou, quem ganhou e por quê."""

    id: str
    objective: str
    capability: str = ""
    strategy: str = "equilibrado"
    bids: list[Bid] = Field(default_factory=list)
    chosen: str | None = None
    reason: str = ""
    task_id: str | None = None
    created_by: str = "cli"
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def contenders(self) -> list[Bid]:
        return [bid for bid in self.bids if bid.allowed]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "objetivo": self.objective,
            "estratégia": self.strategy,
            "capacidade": self.capability or "-",
            "disputaram": len(self.contenders),
            "vetados": len(self.bids) - len(self.contenders),
            "escolhido": self.chosen or "-",
            "motivo": self.reason or "-",
            "task": self.task_id,
            "quando": self.created_at.isoformat(),
        }


class DatabaseTrigger(BaseModel):
    """Gatilho de banco: uma linha muda, um evento do Runtime acontece.

    `when` é uma expressão SQL avaliada pelo SQLite (`NEW.status = 'blocked'`),
    mas **não é SQL livre**: só entram colunas da lista branca da tabela e
    operadores de comparação. O gatilho é instalado como um `CREATE TRIGGER`
    de verdade — o banco avisa, o Runtime só lê a fila.
    """

    id: str
    name: str
    table: str
    event: str = "insert"          # insert | update | delete
    when: str = ""                 # ex.: NEW.status = 'blocked'
    emit: str = ""                 # ex.: db.task_blocked
    enabled: bool = True
    created_by: str = "cli"
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def trigger_name(self) -> str:
        return f"egr_{self.id}"

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nome": self.name,
            "tabela": self.table,
            "evento": self.event,
            "quando": self.when or "sempre",
            "emite": self.emit,
            "ativa": self.enabled,
            "criado_por": self.created_by,
        }


class DatabaseEvent(BaseModel):
    """Linha da fila: o que o banco avisou e se o Runtime já viu."""

    id: str
    trigger_id: str
    event: str
    table: str
    row_id: str = ""
    payload: dict = Field(default_factory=dict)
    processed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


__all__ = ["STRATEGIES", "Bid", "DatabaseEvent", "DatabaseTrigger", "Negotiation"]
