"""Planner: turns an objective into a governed plan (Tool calls).

The model only *proposes*. Every step is authorized by the Policy Engine and
executed by the Tool Runtime.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from ..core.errors import EGRError
from ..domain.agent import AgentSpec
from ..domain.enums import Environment
from ..models.gateway import Message

PLANNER_SYSTEM = """Voce e o planejador do Enterprise AGI Runtime (EGR).

Voce nao executa nada: voce propoe um plano. O Runtime autoriza cada passo
(politicas), executa (ferramentas), registra (auditoria) e lembra (memoria).

Responda SOMENTE com JSON valido, sem texto adicional, neste formato:
{{
  "objective": "<objetivo>",
  "steps": [
    {{"id": "s1", "tool": "<nome da ferramenta>", "args": {{...}}, "rationale": "<por que>"}}
  ],
  "final_answer": "<resposta final esperada>"
}}

Regras:
- Use apenas ferramentas do catalogo recebido.
- No maximo {max_steps} passos.
- Nunca use caminhos absolutos fora do workspace.
- Prefira ferramentas locais; chamadas externas sao governadas por politica.
- Se nenhuma ferramenta for necessaria, devolva "steps": [].
"""

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class PlanParseError(EGRError):
    pass


class PlanStep(BaseModel):
    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    action: str | None = None


class Plan(BaseModel):
    objective: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    final_answer: str = ""
    provider: str | None = None
    model: str | None = None


def build_plan_messages(
    agent: AgentSpec,
    objective: str,
    tool_catalog: str,
    memory_context: str,
    environment: Environment,
    max_steps: int = 8,
) -> list[Message]:
    system = PLANNER_SYSTEM.format(max_steps=max_steps)
    user = (
        f"Agente: {agent.id} (v{agent.version})\n"
        f"Objetivo do agente: {agent.objective or '-'}\n"
        f"Ambiente: {environment}\n\n"
        f"Memoria relevante:\n{memory_context}\n\n"
        f"Catalogo de ferramentas:\n{tool_catalog}\n\n"
        f"Objetivo da tarefa:\n{objective}\n"
    )
    if agent.system_prompt:
        system = f"{system}\n\nInstrucoes adicionais do agente:\n{agent.system_prompt}"
    return [Message(role="system", content=system), Message(role="user", content=user)]


def parse_plan(text: str, objective: str = "") -> Plan:
    raw = (text or "").strip()
    if not raw:
        raise PlanParseError("empty model response")
    candidates = [raw]
    block = _JSON_BLOCK.search(raw)
    if block:
        candidates.insert(0, block.group(1))
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(data, dict):
            last_error = ValueError("plan is not an object")
            continue
        data.setdefault("objective", objective)
        return Plan.model_validate(data)
    raise PlanParseError(f"could not parse plan: {last_error}")


def fallback_plan(objective: str) -> Plan:
    """Used when the model fails: inspect locally, then finish with what we know."""

    return Plan(
        objective=objective,
        steps=[
            PlanStep(
                id="s1",
                tool="filesystem.list",
                args={"path": "documents", "recursive": True},
                rationale="fallback: inspecionar o workspace antes de agir",
            )
        ],
        final_answer="Plano de fallback executado (o modelo nao produziu um plano valido).",
        provider="fallback",
        model=None,
    )
