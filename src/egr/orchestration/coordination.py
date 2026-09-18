"""Lacuna 6b: o coordenador — quem executa o quê, e com qual motivo.

Dois movimentos:

- **negociação**: os agentes elegíveis declaram um lance (custo estimado e fila)
  e a estratégia escolhe o vencedor. Sem modelo opinando, sem sorteio: o mesmo
  estado dá sempre o mesmo resultado, e a escolha fica registrada;
- **handoff**: uma task passa de um agente para outro com motivo. Handoff é
  remédio, não hábito — há limite por task e o histórico aparece no contexto.

Em nenhum dos dois o agente decide sozinho: política, permissões e ambiente
continuam valendo, e quem não pode receber a task é vetado com motivo escrito.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import AuthorizationError, ConfigError
from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.coordination import STRATEGIES, Bid, Negotiation
from ..domain.enums import EventType, TaskStatus
from ..security.rbac import TASK_HANDOFF, has_permission

#: quantos caracteres de objetivo viram "um token" (estimativa declarada)
CHARS_PER_TOKEN = 4
#: tokens de resposta esperados para uma task simples
EXPECTED_OUTPUT_TOKENS = 400


class Coordinator:
    """Escolhe agente por lance e faz o handoff com motivo."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    # ---- negociação ---------------------------------------------------
    @staticmethod
    def disabled(agent: Any) -> bool:
        """Agente fora de jogo: `enabled` (se o manifesto tiver) ou metadata.disabled."""

        if hasattr(agent, "enabled") and not agent.enabled:
            return True
        return bool((getattr(agent, "metadata", None) or {}).get("disabled"))

    def candidates(self, capability: str | None = None, *, include_vetoed: bool = False) -> list[Any]:
        """Agentes que podem disputar — ou todos, para mostrar quem não pode.

        `include_vetoed` é o que a negociação usa: quem não pode aparece na
        lista com o motivo do veto, em vez de desaparecer sem explicação.
        """

        runtime = self.runtime
        environment = str(runtime.settings.config.environment)
        pool = []
        for _name, agent in sorted(runtime.agents.items()):
            if not include_vetoed:
                if self.disabled(agent):
                    continue
                declared = str(getattr(agent, "environment", "") or "")
                if declared and declared not in ("", environment):
                    continue
                if capability and str(getattr(agent.model, "capability", "") or "") != capability:
                    continue
            pool.append(agent)
        return pool

    def bid(self, agent: Any, objective: str, *, capability: str | None = None) -> Bid:
        """O lance de um agente: custo estimado, fila e se ele pode executar."""

        name = str(getattr(agent, "id", "") or getattr(agent, "name", ""))
        agent_capability = str(getattr(agent.model, "capability", "") or "")
        veto = self._veto(agent, capability)
        if veto:
            return Bid(agent=name, capability=agent_capability, allowed=False, veto=veto)

        cost = self.estimate_cost(agent, objective)
        queue = self.queue_of(name)
        reason = f"custo {cost:.6f} · fila {queue}"
        return Bid(
            agent=name,
            capability=agent_capability,
            estimated_cost=cost,
            queue=queue,
            allowed=True,
            reason=reason,
        )

    def negotiate(
        self,
        objective: str,
        *,
        capability: str | None = None,
        strategy: str | None = None,
        agents: list[str] | None = None,
        task_id: str | None = None,
        actor: str = "cli",
    ) -> Negotiation:
        """Roda uma rodada e devolve a negociação (com o vencedor e o motivo).

        `strategy`: `equilibrado` (custo + fila) · `menor_custo` · `menor_fila` ·
        `declarado` (o `coordination.default_agent`, sem disputa).
        """

        policy = self.runtime.settings.config.coordination
        if not policy.enabled:
            raise ConfigError("coordenação negociada está desligada (`coordination.enabled: false`)")
        strategy = strategy or policy.strategy
        if strategy not in STRATEGIES:
            raise ConfigError(f"estratégia inválida: {strategy} (use {', '.join(STRATEGIES)})")

        pool = self.candidates(capability, include_vetoed=True)
        if agents:
            wanted = {str(item) for item in agents}
            pool = [agent for agent in pool if str(getattr(agent, "id", "")) in wanted]

        negotiation = Negotiation(
            id=new_id("neg"),
            objective=objective,
            capability=capability or "",
            strategy=strategy,
            task_id=task_id,
            created_by=actor,
        )

        if strategy == "declarado":
            fixed = policy.default_agent
            agent = next((item for item in self.runtime.agents.values() if str(getattr(item, "id", "")) == fixed), None)
            if agent is None:
                raise ConfigError(f"agente declarado '{fixed}' não está carregado (ou não existe)")
            bid = self.bid(agent, objective, capability=capability)
            negotiation.bids = [bid]
            negotiation.chosen = bid.agent if bid.allowed else None
            negotiation.reason = f"estratégia declarada: {fixed}"
            if not bid.allowed:
                negotiation.reason = f"agente declarado vetado: {bid.veto}"
        else:
            bids = [self.bid(agent, objective, capability=capability) for agent in pool]
            for bid in bids:
                bid.score = self._score(bid, bids, strategy)
            contenders = sorted(
                [bid for bid in bids if bid.allowed], key=lambda item: (item.score, item.agent)
            )
            negotiation.bids = sorted(bids, key=lambda item: (not item.allowed, item.score, item.agent))
            if not contenders:
                negotiation.reason = "nenhum agente elegível: " + "; ".join(
                    f"{bid.agent} ({bid.veto or 'vetado'})" for bid in bids[:3]
                ) or "nenhum agente carregado"
            else:
                winner = contenders[0]
                negotiation.chosen = winner.agent
                negotiation.reason = (
                    f"menor nota por '{strategy}': {winner.score:.6f} "
                    f"(custo {winner.estimated_cost:.6f}, fila {winner.queue})"
                )

        self.runtime.negotiations.save(negotiation)
        self.runtime.audit.record(
            EventType.TASK_NEGOTIATED,
            actor=actor,
            environment=str(self.runtime.settings.config.environment),
            payload={
                "negociação": negotiation.id,
                "estratégia": strategy,
                "escolhido": negotiation.chosen,
                "disputaram": len(negotiation.contenders),
                "vetados": len(negotiation.bids) - len(negotiation.contenders),
                "motivo": negotiation.reason,
                "objetivo": objective[:200],
            },
        )
        return negotiation

    def handoff(self, task_id: str, to_agent: str, *, reason: str = "", actor: str = "cli") -> Any:
        """Passa a task para outro agente — com motivo, limite e trilha."""

        runtime = self.runtime
        task = runtime.tasks.get(task_id)
        if task is None:
            raise ConfigError(f"task não encontrada: {task_id}")
        if task.is_terminal:
            raise ConfigError(f"task {task_id} está {task.status}: não dá mais para repassar")

        self._authorize(actor)

        target = runtime.agents.get(to_agent)
        if target is None:
            raise ConfigError(f"agente '{to_agent}' não está carregado")
        if self.disabled(target):
            raise ConfigError(f"agente '{to_agent}' está desabilitado")
        if str(task.agent_id) == to_agent:
            raise ConfigError(f"task {task_id} já está com '{to_agent}'")

        limit = runtime.settings.config.coordination.max_handoffs
        history = list(task.context.get("handoffs") or [])
        if len(history) >= limit:
            raise ConfigError(
                f"task {task_id} já foi repassada {len(history)} vez(es) (limite {limit}): "
                "trocar de agente sem parar é fugir do problema — resolva a causa"
            )

        previous = str(task.agent_id)
        task.agent_id = to_agent
        history.append(
            {
                "de": previous,
                "para": to_agent,
                "motivo": reason or "-",
                "por": actor,
                "quando": utcnow().isoformat(),
            }
        )
        task.context["handoffs"] = history
        task.updated_at = utcnow()
        if task.status in (TaskStatus.WAITING, TaskStatus.REQUIRES_APPROVAL):
            # o agente novo replaneja: a espera antiga não vale mais
            task.status = TaskStatus.PENDING
            task.error = None
        saved = runtime.tasks.save(task)

        runtime.audit.record(
            EventType.TASK_HANDOFF,
            actor=actor,
            task_id=task.id,
            agent_id=to_agent,
            environment=str(task.environment),
            payload={
                "task": task.id,
                "de": previous,
                "para": to_agent,
                "motivo": reason or "-",
                "repasses": len(history),
                "limite": limit,
            },
        )
        return saved

    # ---- apoio --------------------------------------------------------
    def estimate_cost(self, agent: Any, objective: str) -> float:
        """Custo estimado: tokens do objetivo × preço do provedor do agente."""

        capability = str(getattr(agent.model, "capability", "") or "reasoning")
        tokens = max(1, len(objective) // CHARS_PER_TOKEN) + EXPECTED_OUTPUT_TOKENS
        try:
            providers = self.runtime.gateway.candidates(capability)
        except Exception:  # sem provedor: custo estimado zero, veto vem do _veto
            return 0.0
        provider = next(
            (item for item in providers if item.name == str(getattr(agent.model, "provider", "") or "")),
            providers[0] if providers else None,
        )
        if provider is None:
            return 0.0
        return round((tokens / 1000.0) * float(getattr(provider, "unit_cost", 0.0) or 0.0), 8)

    def queue_of(self, agent_id: str) -> int:
        """Quantas tasks vivas o agente já tem: fila é custo também."""

        alive = {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.WAITING, TaskStatus.REQUIRES_APPROVAL}
        return sum(
            1
            for task in self.runtime.tasks.list(limit=200)
            if str(task.agent_id) == agent_id and task.status in alive
        )

    def status(self) -> dict[str, Any]:
        negotiations = self.runtime.negotiations.list(limit=10)
        return {
            "estratégia": self.runtime.settings.config.coordination.strategy,
            "ativa": self.runtime.settings.config.coordination.enabled,
            "agentes_elegíveis": [str(getattr(agent, "id", "")) for agent in self.candidates()],
            "negociações": [item.summary() for item in negotiations],
            "total": len(self.runtime.negotiations.list(limit=100)),
        }

    # ---- internos -----------------------------------------------------
    def _veto(self, agent: Any, capability: str | None) -> str:
        """Por que este agente não pode executar ('' = pode)."""

        if self.disabled(agent):
            return "agente desabilitado"
        environment = str(self.runtime.settings.config.environment)
        declared = str(getattr(agent, "environment", "") or "")
        if declared and declared != environment:
            return f"ambiente '{declared}' ≠ '{environment}'"
        if capability and str(getattr(agent.model, "capability", "") or "") != capability:
            return f"capacidade '{getattr(agent.model, 'capability', '')}' ≠ '{capability}'"
        try:
            self.runtime.gateway.candidates(str(getattr(agent.model, "capability", "") or "reasoning"))
        except Exception:
            return "sem provedor para a capacidade do agente"
        return ""

    @staticmethod
    def _score(bid: Bid, bids: list[Bid], strategy: str) -> float:
        """Nota por estratégia — menor ganha. Normalizado para caber em 0..2."""

        costs = [item.estimated_cost for item in bids if item.allowed] or [0.0]
        queues = [float(item.queue) for item in bids if item.allowed] or [0.0]
        cost = bid.estimated_cost / max(max(costs), 1e-9)
        queue = float(bid.queue) / max(max(queues), 1.0)
        if strategy == "menor_custo":
            return cost
        if strategy == "menor_fila":
            return queue
        return cost + queue

    def _authorize(self, actor: str) -> None:
        """Handoff é ato governado: identidade verificada quando exigida."""

        security = self.runtime.settings.config.security
        if not security.identity_required:
            return
        principal = self.runtime.identity.resolve(actor.replace("human:", ""))
        if principal is None:
            raise AuthorizationError(
                f"identidade não verificada: repassar task exige um principal autenticado ('{actor}')"
            )
        if not has_permission(principal, TASK_HANDOFF):
            raise AuthorizationError(f"'{principal.id}' não tem a permissão '{TASK_HANDOFF}'")


__all__ = ["Coordinator"]
