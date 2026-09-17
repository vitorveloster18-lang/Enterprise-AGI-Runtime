"""Gateway de canais: mensagem de fora → trabalho governado por dentro.

O Gateway não é um atalho. Ele faz quatro coisas, nesta ordem:

1. **normaliza** a mensagem (`InboundMessage`);
2. **identifica** quem fala (`ChannelBinding` → `Principal` do Runtime);
3. **autoriza** (pareamento, lista branca, ritmo e permissão RBAC);
4. **entrega** o objetivo ao Runtime — que continua decidindo política,
   aprovação, custo e memória.

Quando algo falha, a falha é a resposta: canal sem pareamento recebe o código de
pareamento, remetente sem permissão recebe a negativa, e tudo vai para a trilha.
"""

from __future__ import annotations

import contextlib
import secrets
from datetime import timedelta
from typing import Any

from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.channel import ChannelBinding, GatewayMessage, GatewayReply, InboundMessage
from ..domain.enums import BindingStatus, EventType, TaskStatus
from ..security.rbac import APPROVAL_DECIDE, GATEWAY_USE, TASK_READ, TASK_SUBMIT, has_permission
from ..security.redaction import redact_text

HELP = (
    "comandos: /ajuda · /quem · /status · /tasks · /parear\n"
    "• /run <objetivo> — executa uma task agora\n"
    "• /aprovar <id> — decide uma aprovação pendente (canal habilitado)\n"
    "• qualquer outra mensagem é tratada como objetivo de task"
)


class GatewayService:
    """O único ponto por onde uma mensagem externa entra no Runtime."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.bindings = runtime.gateway_bindings
        self.messages = runtime.gateway_messages
        self._channels: dict[str, Any] = {}

    # ---- canais ------------------------------------------------------
    @property
    def config(self):
        return self.runtime.settings.config.gateway

    def register(self, channel: Any) -> None:
        """Registra uma instância de canal (telegram, slack, web, console)."""

        self._channels[channel.name] = channel

    def channel(self, name: str) -> Any | None:
        return self._channels.get(name)

    @property
    def channel_names(self) -> list[str]:
        return list(self._channels)

    def select(self, names: list[str] | None = None) -> list[Any]:
        """Canais pedidos pelo nome (ou todos os registrados)."""

        if not names:
            return list(self._channels.values())
        selected = [self._channels[name] for name in names if name in self._channels]
        for name in names or []:
            if name not in self._channels:
                raise KeyError(f"canal não registrado: {name}")
        return selected

    def channels(self) -> list[dict[str, Any]]:
        """Estado de cada canal: configurado, registrado e pronto para falar."""

        declared = {item.name: item for item in self.config.channels}
        rows = []
        for name, config in declared.items():
            instance = self._channels.get(name)
            rows.append(
                {
                    "nome": name,
                    "tipo": config.type,
                    "habilitado": bool(config.enabled and self.config.enabled),
                    "instanciado": instance is not None,
                    "agente": config.default_agent or "-",
                    "ambiente": config.environment or str(self.runtime.settings.environment),
                    "lista_branca": len(config.allowed_chat_ids),
                    "decisões": bool(config.allow_decisions),
                    "pareados": len(self.bindings.list(channel=name, status="active", limit=500)),
                }
            )
        for name in self._channels:
            if name not in declared:
                rows.append(
                    {
                        "nome": name,
                        "tipo": getattr(self._channels[name], "kind", "?"),
                        "habilitado": True,
                        "instanciado": True,
                        "agente": "-",
                        "ambiente": str(self.runtime.settings.environment),
                        "lista_branca": 0,
                        "decisões": False,
                        "pareados": len(self.bindings.list(channel=name, status="active", limit=500)),
                    }
                )
        return rows

    def config_for(self, name: str):
        for item in self.config.channels:
            if item.name == name:
                return item
        return None

    # ---- pareamento ---------------------------------------------------
    def binding_for(self, channel: str, external_id: str, *, display_name: str = "") -> ChannelBinding:
        """O binding existe? senão nasce pendente (default deny)."""

        identifier = f"{channel}:{external_id}"
        binding = self.bindings.get(identifier)
        if binding is None:
            binding = ChannelBinding(
                id=identifier,
                channel=channel,
                external_id=external_id,
                display_name=display_name,
                status=BindingStatus.PENDING,
                roles=list(self.config.default_roles),
                pairing_code=ChannelBinding.new_code(),
            )
            self.bindings.save(binding)
            self.runtime.audit.record(
                EventType.GATEWAY_PAIRED,
                actor=external_id,
                environment=str(self.runtime.settings.environment),
                payload={"binding": identifier, "status": "pending", "ação": "descoberto"},
            )
        elif display_name and not binding.display_name:
            binding.display_name = display_name
        return binding

    def pair(
        self,
        channel: str,
        external_id: str,
        *,
        roles: list[str] | None = None,
        code: str | None = None,
        actor: str = "cli",
        display_name: str = "",
    ) -> ChannelBinding:
        """Pareia um remetente: cria (ou atualiza) o Principal com papéis."""

        binding = self.binding_for(channel, external_id, display_name=display_name)
        if code and binding.pairing_code and code.strip().upper() != binding.pairing_code.upper():
            raise ValueError(f"código de pareamento inválido para {binding.id}")
        if binding.status == BindingStatus.BLOCKED and not roles:
            raise ValueError(f"{binding.id} está bloqueado: desbloqueie com --role")

        wanted = list(roles or self.config.default_roles)
        binding.roles = wanted
        binding.status = BindingStatus.ACTIVE
        binding.paired_by = actor
        binding.display_name = display_name or binding.display_name
        binding.principal_id = self._ensure_principal(binding, actor=actor)
        self.bindings.save(binding)
        self.runtime.audit.record(
            EventType.GATEWAY_PAIRED,
            actor=actor,
            environment=str(self.runtime.settings.environment),
            payload={
                "binding": binding.id,
                "principal": binding.principal_id,
                "papéis": sorted(wanted),
                "status": "active",
            },
        )
        return binding

    def unpair(self, channel: str, external_id: str, *, actor: str = "cli", block: bool = False) -> ChannelBinding:
        """Remove o pareamento (e, se pedido, bloqueia o remetente)."""

        identifier = f"{channel}:{external_id}"
        binding = self.bindings.get(identifier)
        if binding is None:
            raise ValueError(f"sem pareamento para {identifier}")

        binding.status = BindingStatus.BLOCKED if block else BindingStatus.PENDING
        binding.pairing_code = ""
        binding.paired_by = None
        self.bindings.save(binding)
        if block and binding.principal_id:
            # principal já removido ou desabilitado: o binding bloqueado basta
            with contextlib.suppress(Exception):
                self.runtime.identity.set_status(binding.principal_id, "disabled", actor=actor)
        self.runtime.audit.record(
            EventType.GATEWAY_UNPAIRED,
            actor=actor,
            environment=str(self.runtime.settings.environment),
            payload={"binding": binding.id, "bloqueado": block},
        )
        return binding

    def list_bindings(self, status: str | None = None, channel: str | None = None, limit: int = 50):
        return self.bindings.list(status=status, channel=channel, limit=limit)

    def _ensure_principal(self, binding: ChannelBinding, *, actor: str = "cli") -> str:
        """Cada remetente pareado é um Principal: RBAC não inventa identidade."""

        principal_id = binding.principal_id or f"{binding.channel}.{binding.external_id}"
        existing = self.runtime.identity.get(principal_id)
        if existing is None:
            created = self.runtime.identity.create_principal(
                principal_id,
                name=binding.display_name or binding.external_id,
                roles=binding.roles,
                actor=actor,
                metadata={"canal": binding.channel, "remetente": binding.external_id},
            )
            return created.id
        self.runtime.identity.set_roles(principal_id, binding.roles, actor=actor)
        return existing.id

    # ---- mensagens ----------------------------------------------------
    def handle(
        self,
        channel: str,
        external_id: str,
        text: str,
        *,
        display_name: str = "",
        reply_to: str = "",
    ) -> GatewayReply:
        return self.handle_inbound(
            InboundMessage(
                channel=channel,
                external_id=external_id,
                text=text,
                display_name=display_name,
                reply_to=reply_to,
            )
        )

    def handle_inbound(self, message: InboundMessage) -> GatewayReply:
        """Uma mensagem entra, uma resposta sai — e o meio é governado."""

        text = (message.text or "").strip()
        if len(text) > self.config.max_message_chars:
            text = text[: self.config.max_message_chars]

        if not self.config.enabled:
            return self._deny(message, "gateway desabilitado na configuração", text)

        config = self.config_for(message.channel)
        if config is not None and not config.enabled:
            return self._deny(message, f"canal '{message.channel}' desabilitado", text)

        binding = self.binding_for(
            message.channel, message.external_id, display_name=message.display_name
        )
        self._log("in", message, text)

        if not self._allowed_sender(config, message.external_id):
            return self._deny(message, "remetente fora da lista branca do canal", text)

        if binding.status == BindingStatus.BLOCKED:
            return self._deny(message, "remetente bloqueado por um operador", text)

        if self.config.require_pairing and not binding.active:
            return self._deny(
                message,
                f"sem pareamento: peça a um operador `egr gateway pair {message.channel} "
                f"{message.external_id} --code {binding.pairing_code}`",
                text,
            )

        limited = self._rate_limit(binding)
        if limited:
            return self._deny(message, limited, text)

        principal = self.runtime.identity.get(binding.principal_id or "")
        if principal is None:
            binding.principal_id = self._ensure_principal(binding)
            principal = self.runtime.identity.get(binding.principal_id)
        if principal is None or not principal.active:
            return self._deny(message, "principal do remetente não está ativo", text)
        if not has_permission(principal, GATEWAY_USE):
            return self._deny(message, f"papel sem a permissão '{GATEWAY_USE}'", text)

        binding.touch()
        self.bindings.save(binding)

        command, argument = self._split(text)
        if command in ("ajuda", "help", "start"):
            reply = GatewayReply(text=HELP, channel=message.channel, external_id=message.external_id, command=command)
        elif command == "quem":
            reply = self._who(binding, principal)
        elif command == "parear":
            reply = GatewayReply(
                text=(
                    f"status: {binding.status} · código: {binding.pairing_code or '-'}\n"
                    f"para parear: egr gateway pair {message.channel} {message.external_id} "
                    f"--code {binding.pairing_code or '...'}"
                ),
                channel=message.channel,
                external_id=message.external_id,
                command=command,
            )
        elif command == "status":
            reply = self._status(message)
        elif command == "tasks":
            reply = self._tasks(message, principal)
        elif command == "run":
            reply = self._run(message, principal, argument or "", binding, config)
        elif command == "aprovar":
            reply = self._approve(message, principal, argument, config)
        else:
            reply = self._run(message, principal, text, binding, config)

        self._log("out", message, reply.text, task_id=reply.task_id, denied=reply.denied, reason=reply.reason)
        return reply

    # ---- comandos -----------------------------------------------------
    def _who(self, binding: ChannelBinding, principal) -> GatewayReply:
        return GatewayReply(
            text=(
                f"você é {principal.id} ({principal.name or '-'}) pelo canal {binding.channel}\n"
                f"papéis: {', '.join(sorted(principal.roles))}\n"
                f"pareamento: {binding.status} · mensagens: {binding.message_count}"
            ),
            channel=binding.channel,
            external_id=binding.external_id,
            command="quem",
        )

    def _status(self, message: InboundMessage) -> GatewayReply:
        status = self.runtime.status()
        counts = status.get("counts", {})
        by_status = counts.get("tasks", {}) if isinstance(counts.get("tasks"), dict) else {}
        return GatewayReply(
            text=(
                f"ambiente {status.get('environment', '-')} · enterprise {status['enterprise']['id']}\n"
                f"tasks: {sum(by_status.values()) if by_status else 0} "
                f"(concluídas: {by_status.get('completed', 0)})\n"
                f"agentes: {counts.get('agents', 0)} · ferramentas: {counts.get('tools', 0)}\n"
                f"aprovações pendentes: {counts.get('approvals_pending', 0)}"
            ),
            channel=message.channel,
            external_id=message.external_id,
            command="status",
        )

    def _tasks(self, message: InboundMessage, principal) -> GatewayReply:
        if not has_permission(principal, TASK_READ):
            return GatewayReply(
                text="seu papel não lê tasks",
                channel=message.channel,
                external_id=message.external_id,
                denied=True,
                reason="sem permissão task.read",
                command="tasks",
            )
        tasks = self.runtime.tasks.list(limit=5)
        if not tasks:
            return GatewayReply(
                text="nenhuma task ainda",
                channel=message.channel,
                external_id=message.external_id,
                command="tasks",
            )
        lines = [
            f"• {task.id} [{task.status}] {task.objective[:60]}"
            for task in tasks
        ]
        return GatewayReply(
            text="últimas tasks:\n" + "\n".join(lines),
            channel=message.channel,
            external_id=message.external_id,
            command="tasks",
        )

    def _run(self, message: InboundMessage, principal, objective: str, binding: ChannelBinding, config) -> GatewayReply:
        if not objective:
            return GatewayReply(
                text="diga o que fazer: /run <objetivo> (ou escreva o objetivo direto)",
                channel=message.channel,
                external_id=message.external_id,
                command="run",
            )
        if not has_permission(principal, TASK_SUBMIT):
            return GatewayReply(
                text=(
                    f"seu papel ({', '.join(sorted(principal.roles))}) não envia tasks.\n"
                    "peça a um operador: egr gateway pair "
                    f"{message.channel} {message.external_id} --role operator"
                ),
                channel=message.channel,
                external_id=message.external_id,
                denied=True,
                reason="sem permissão task.submit",
                command="run",
            )

        environment = (config.environment if config else "") or str(self.runtime.settings.environment)
        task = self.runtime.submit(
            objective,
            agent_id=(config.default_agent if config else "") or None,
            environment=environment,
            created_by=principal.id,
        )
        return GatewayReply(
            text=self._task_answer(task),
            channel=message.channel,
            external_id=message.external_id,
            task_id=task.id,
        )

    def _approve(self, message: InboundMessage, principal, approval_id: str, config) -> GatewayReply:
        if config is None or not config.allow_decisions:
            return GatewayReply(
                text="este canal não decide aprovações (habilite allow_decisions no canal)",
                channel=message.channel,
                external_id=message.external_id,
                denied=True,
                reason="canal sem allow_decisions",
                command="aprovar",
            )
        if not has_permission(principal, APPROVAL_DECIDE):
            return GatewayReply(
                text=f"seu papel não decide aprovações ('{APPROVAL_DECIDE}' necessária)",
                channel=message.channel,
                external_id=message.external_id,
                denied=True,
                reason="sem permissão approval.decide",
                command="aprovar",
            )
        if not approval_id:
            return GatewayReply(
                text="uso: /aprovar <id da aprovação>",
                channel=message.channel,
                external_id=message.external_id,
                command="aprovar",
            )
        try:
            approval = self.runtime.approve(approval_id, decided_by=principal.id, note="decidido pelo canal")
        except Exception as exc:  # aprovação inexistente ou já decidida
            return GatewayReply(
                text=f"não foi possível aprovar {approval_id}: {exc}",
                channel=message.channel,
                external_id=message.external_id,
                denied=True,
                reason=str(exc),
                command="aprovar",
            )
        return GatewayReply(
            text=f"aprovação {approval.id} decidida por {principal.id}",
            channel=message.channel,
            external_id=message.external_id,
            command="aprovar",
        )

    # ---- resposta -----------------------------------------------------
    def _task_answer(self, task) -> str:
        if task.status == TaskStatus.COMPLETED:
            answer = (task.result.answer if task.result else "") or "concluído sem resposta"
            return self._scrub(answer)
        if task.status in (TaskStatus.REQUIRES_APPROVAL, TaskStatus.WAITING):
            pending = self.runtime.approvals.pending_for_task(task.id)
            lines = [f"task {task.id} esperando aprovação humana:"]
            for approval in pending:
                lines.append(f"• {approval.id} — {approval.tool} {approval.args}")
            lines.append("decida no console, na API, ou com /aprovar <id> neste canal")
            return "\n".join(lines)
        if task.status == TaskStatus.FAILED:
            error = (task.result.error if task.result else None) or task.error or "falha sem detalhe"
            return f"task {task.id} falhou: {self._scrub(str(error))}"
        return f"task {task.id} está {task.status}"

    def _scrub(self, text: str) -> str:
        cleaned = redact_text(text) if self.config.redact else text
        if len(cleaned) > self.config.max_reply_chars:
            return cleaned[: self.config.max_reply_chars - 3] + "..."
        return cleaned

    # ---- apoio --------------------------------------------------------
    def _split(self, text: str) -> tuple[str, str]:
        if not text.startswith("/"):
            return "", text
        head, _, rest = text[1:].partition(" ")
        command = head.split("@")[0].lower()
        return command, rest.strip()

    def _allowed_sender(self, config, external_id: str) -> bool:
        if config is None or not config.allowed_chat_ids:
            return True
        return str(external_id) in {str(item) for item in config.allowed_chat_ids}

    def _rate_limit(self, binding: ChannelBinding) -> str:
        """Ritmo contado no banco: reiniciar o processo não limpa a janela."""

        limit = self.config.rate_limit_per_minute
        if limit <= 0:
            return ""
        hits = self.messages.count_since(
            utcnow() - timedelta(minutes=1),
            channel=binding.channel,
            external_id=binding.external_id,
            direction="in",
        )
        if hits > limit:
            return f"ritmo excedido: {limit} mensagens por minuto"
        return ""

    def _deny(self, message: InboundMessage, reason: str, text: str) -> GatewayReply:
        reply = GatewayReply(
            text=f"não executado: {reason}",
            channel=message.channel,
            external_id=message.external_id,
            denied=True,
            reason=reason,
        )
        self._log("in", message, text, denied=True, reason=reason)
        self._log("out", message, reply.text, denied=True, reason=reason)
        self.runtime.audit.record(
            EventType.GATEWAY_DENIED,
            actor=f"{message.channel}:{message.external_id}",
            environment=str(self.runtime.settings.environment),
            payload={"canal": message.channel, "motivo": reason, "texto": text[:200]},
        )
        return reply

    def _log(
        self,
        direction: str,
        message: InboundMessage,
        text: str,
        *,
        task_id: str | None = None,
        denied: bool = False,
        reason: str = "",
    ) -> GatewayMessage:
        stored = GatewayMessage(
            id=new_id("message"),
            channel=message.channel,
            direction=direction,
            external_id=message.external_id,
            text=self._scrub(text)[:1000],
            task_id=task_id,
            denied=denied,
            reason=reason,
        )
        self.messages.save(stored)
        self.runtime.audit.record(
            EventType.GATEWAY_MESSAGE_RECEIVED if direction == "in" else EventType.GATEWAY_MESSAGE_SENT,
            actor=f"{message.channel}:{message.external_id}",
            environment=str(self.runtime.settings.environment),
            task_id=task_id,
            payload={
                "canal": message.channel,
                "direção": direction,
                "tamanho": len(text),
                "recusada": denied,
                "motivo": reason,
            },
        )
        return stored

    # ---- estado -------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "habilitado": bool(self.config.enabled),
            "pareamento_exigido": bool(self.config.require_pairing),
            "papéis_padrão": sorted(self.config.default_roles),
            "ritmo_por_minuto": self.config.rate_limit_per_minute,
            "redação": bool(self.config.redact),
            "canais": self.channels(),
            "pareamentos": {
                "total": self.bindings.count(),
                "por_status": self.bindings.stats(),
                "recentes": [binding.summary() for binding in self.bindings.list(limit=5)],
            },
            "mensagens": {
                "total": self.messages.count(),
                "por_direção": self.messages.stats(),
                "recentes": [item.summary() for item in self.messages.list(limit=5)],
            },
        }


def pairing_code() -> str:
    """Código curto de pareamento (o operador digita no CLI)."""

    return secrets.token_hex(3).upper()


__all__ = ["HELP", "GatewayService", "pairing_code"]
