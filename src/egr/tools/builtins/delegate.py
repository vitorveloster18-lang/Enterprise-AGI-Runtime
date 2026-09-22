"""task.delegate: o orquestrador repassa, revisa e só então aceita.

Fluxo síncrono, tudo auditado:

  1. cria a task filha (``parent_id`` ligado) e executa pelo motor governado —
     a filha passa por política, aprovação e orçamento como qualquer task;
  2. filha aguardando aprovação → volta ``waiting_approval`` (o humano decide,
     o pai continua);
  3. filha concluída → revisão (chamada ao modelo com os critérios) →
     ``accept`` | ``revise`` | ``escalate``;
  4. ``revise`` repete a filha com o feedback (no máximo ``max_rounds``);
  5. ``escalate`` abre uma aprovação humana **na área da filha** e volta
     ``escalated`` — sem retomada automática (veredito, não pausa).

Sem aprovação automática em nenhum ponto: ``escalate`` e ``waiting_approval``
param no humano (``egr approval decide`` / ctrl+a), como manda o contrato.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from ...domain.enums import EventType, RiskLevel, TaskStatus
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext

MAX_DELEGATE_DEPTH = 2
MAX_ROUNDS_CAP = 3

_ACCEPT = {"accept", "aceitar", "aceito", "approve", "approved", "aprovado", "aprovar"}
_REVISE = {"revise", "revisar", "revisao", "revisão", "retry", "refazer", "adjust", "ajustar"}
_ESCALATE = {"escalate", "escalated", "escalar", "human", "humano", "critico", "crítico", "critical"}


def parse_verdict(text: str, on_unclear: str = "escalate") -> tuple[str, str]:
    """Extrai ``(veredito, notas)`` da resposta do revisor.

    Aceita JSON (``{"veredito": ..., "notas": ...}``, com ou sem cerca de
    código), PT ou EN, e o formato de linha ``VEREDITO: <palavra>`` para
    modelos sem modo JSON. Ilegível → ``on_unclear`` (padrão: escalar,
    falhar fechado).
    """

    def normalize(raw: str) -> str | None:
        word = raw.strip().lower()
        if word in _ACCEPT:
            return "accept"
        if word in _REVISE:
            return "revise"
        if word in _ESCALATE:
            return "escalate"
        return None

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        verdict = normalize(str(data.get("veredito") or data.get("verdict") or ""))
        notes = str(data.get("notas") or data.get("notes") or "")
        if verdict:
            return verdict, notes
    match = re.search(r"veredito\s*:\s*([a-záâçéíóú_]+)", (text or ""), re.IGNORECASE)
    if match:
        verdict = normalize(match.group(1))
        if verdict:
            return verdict, (text or "").strip()[:500]
    return on_unclear, f"veredito ilegível ({(text or '').strip()[:120]})"


def review_messages(objective: str, answer: str, criteria: str | None) -> list:
    """Monta as mensagens da revisão (sistema + entrega da filha)."""
    from ...models.gateway import Message

    system = (
        "Você é o revisor orquestrador. Avalie a entrega do subagente contra "
        "o objetivo e os critérios. Responda SOMENTE com JSON: "
        '{"veredito": "accept | revise | escalate", "notas": "<motivo e, se revise, o que corrigir>"}.\n'
        "- accept: entrega cumpre o objetivo e os critérios.\n"
        "- revise: entrega corrigível pelo subagente (diga exatamente o que mudar).\n"
        "- escalate: crítico, sensível ou fora da alçada do subagente (decide o humano)."
    )
    user = f"OBJETIVO:\n{objective}\n\nENTREGA DO SUBAGENTE:\n{answer}"
    if criteria:
        user += f"\n\nCRITÉRIOS DE ACEITE:\n{criteria}"
    return [Message(role="system", content=system), Message(role="user", content=user)]


class DelegateTool(Tool):
    """Delega um objetivo a outro agente e revisa o resultado."""

    spec = ToolSpec(
        name="task.delegate",
        description="Delega um objetivo a outro agente e revisa a entrega (accept/revise/escalate)",
        parameters={
            "agent_id": {"type": "string", "required": True, "help": "agente executor (ex.: finance-agent)"},
            "objective": {"type": "string", "required": True, "help": "objetivo da task filha"},
            "context": {"type": "string", "required": False, "help": "contexto extra anexado ao objetivo"},
            "criteria": {"type": "string", "required": False, "help": "critérios de aceite da revisão"},
            "max_rounds": {"type": "integer", "required": False, "help": "revisões permitidas (0-3, padrão 1)"},
            "on_unclear": {
                "type": "string",
                "required": False,
                "help": "veredito ilegível: escalate (padrão) ou accept",
            },
            "required_role": {"type": "string", "required": False, "help": "papel exigido p/ decidir escalação"},
        },
        risk=RiskLevel.MEDIUM,
        side_effects=True,
    )

    # ---- execução ---------------------------------------------------

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        args = request.args or {}
        runtime = getattr(ctx, "runtime", None)
        if runtime is None:
            return ToolResult(ok=False, error="task.delegate exige contexto do Runtime")
        agent_id = str(args.get("agent_id") or "").strip()
        objective = str(args.get("objective") or "").strip()
        if not agent_id or not objective:
            return ToolResult(ok=False, error="task.delegate exige 'agent_id' e 'objective'")
        agent = runtime.agents.get(agent_id)
        if agent is None:
            return ToolResult(ok=False, error=f"unknown agent '{agent_id}'")
        on_unclear = str(args.get("on_unclear") or "escalate").strip().lower()
        if on_unclear not in ("accept", "escalate"):
            return ToolResult(ok=False, error="on_unclear deve ser 'accept' ou 'escalate'")
        try:
            max_rounds = int(args.get("max_rounds", 1))
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="max_rounds deve ser um inteiro de 0 a 3")
        max_rounds = max(0, min(MAX_ROUNDS_CAP, max_rounds))

        parent = runtime.tasks.get(ctx.task_id) if ctx.task_id else None
        parent_depth = int((parent.context or {}).get("delegate_depth", 0)) if parent else 0
        if parent_depth >= MAX_DELEGATE_DEPTH:
            return ToolResult(
                ok=False,
                error=f"profundidade máxima de delegação atingida ({MAX_DELEGATE_DEPTH})",
            )
        reviewer = runtime.agents.get(ctx.agent_id) if ctx.agent_id else None
        if reviewer is None:
            reviewer = runtime.default_agent()

        criteria = args.get("criteria")
        extra = str(args.get("context") or "").strip()
        required_role = args.get("required_role")
        feedback: str | None = None
        rounds = 0
        while True:
            child_objective = objective
            if extra:
                child_objective += f"\n\nContexto do orquestrador:\n{extra}"
            if feedback:
                child_objective += f"\n\nRevisão anterior — atenda antes de reentregar:\n{feedback}"
            child = runtime.task_engine.create(
                child_objective,
                agent_id,
                environment=ctx.environment,
                created_by=ctx.agent_id or "orchestrator",
                parent_id=parent.id if parent else None,
            )
            child.context["delegate_depth"] = parent_depth + 1
            runtime.tasks.save(child)
            runtime.audit.record(
                EventType.TASK_DELEGATED,
                actor=ctx.agent_id or "orchestrator",
                task_id=parent.id if parent else None,
                agent_id=ctx.agent_id,
                environment=str(ctx.environment),
                payload={"child": child.id, "agent": agent_id, "round": rounds},
            )
            runtime.agent_engine.run(child)
            child = runtime.tasks.get(child.id) or child

            if child.status == TaskStatus.REQUIRES_APPROVAL:
                pending = runtime.approvals.pending_for_task(child.id)
                return ToolResult(
                    ok=True,
                    output={
                        "status": "waiting_approval",
                        "child_id": child.id,
                        "agent_id": agent_id,
                        "rounds": rounds,
                        "approval_id": pending[0].id if pending else None,
                        "note": "filha pausada aguardando o humano; o pai continua",
                    },
                )
            if child.status != TaskStatus.COMPLETED:
                return ToolResult(
                    ok=True,
                    output={
                        "status": "failed",
                        "child_id": child.id,
                        "agent_id": agent_id,
                        "rounds": rounds,
                        "error": (child.result.error if child.result else None) or child.status.value,
                    },
                )
            verdict, notes = self.review_child(runtime, reviewer, child, criteria, on_unclear=on_unclear)
            runtime.audit.record(
                EventType.SUPERVISION_REVIEW,
                actor=reviewer.id,
                task_id=parent.id if parent else None,
                agent_id=reviewer.id,
                environment=str(ctx.environment),
                payload={"child": child.id, "round": rounds, "verdict": verdict},
            )
            answer = (child.result.answer if child.result else "") or ""
            if verdict == "accept":
                return ToolResult(
                    ok=True,
                    output={
                        "status": "accepted",
                        "child_id": child.id,
                        "agent_id": agent_id,
                        "rounds": rounds,
                        "verdict": verdict,
                        "notes": notes,
                        "answer": answer,
                    },
                )
            if verdict == "escalate" or rounds >= max_rounds:
                approval_id = self._escalate(runtime, reviewer, child, notes, required_role)
                return ToolResult(
                    ok=True,
                    output={
                        "status": "escalated",
                        "child_id": child.id,
                        "agent_id": agent_id,
                        "rounds": rounds,
                        "verdict": verdict,
                        "notes": notes,
                        "answer": answer,
                        "approval_id": approval_id,
                        "note": "entrega pausada no humano da área da filha",
                    },
                )
            rounds += 1
            feedback = notes or "revisar a entrega"

    # ---- revisão ----------------------------------------------------

    def review_child(self, runtime, reviewer, child, criteria, on_unclear: str = "escalate"):
        """Roda a revisão no modelo e devolve ``(veredito, notas)``."""
        answer = (child.result.answer if child.result else "") or ""
        messages = review_messages(child.objective, answer, criteria)
        try:
            request = runtime.completion_request(messages, reviewer)
            response = runtime.gateway.complete(request)
            return parse_verdict(response.text or "", on_unclear=on_unclear)
        except Exception as exc:  # revisão indisponível: respeita on_unclear
            return on_unclear, f"revisão indisponível: {exc}"

    def _escalate(self, runtime, reviewer, child, notes, required_role) -> str:
        """Abre aprovação humana vinculada à FILHA (a área dela decide)."""
        request = ToolRequest(
            tool="task.delegate",
            action="review",
            args={"child_id": child.id, "agent_id": child.agent_id},
            task_id=child.id,
            agent_id=reviewer.id,
            environment=child.environment,
        )
        decision = SimpleNamespace(
            required_role=required_role,
            reason=f"revisão do orquestrador: {(notes or 'entrega requer decisão humana')[:280]}",
        )
        approval = runtime.request_approval(
            request,
            decision,
            agent=reviewer,
            task_id=child.id,
            step_id=f"review:{child.id}",
            environment=child.environment,
        )
        return approval.id
