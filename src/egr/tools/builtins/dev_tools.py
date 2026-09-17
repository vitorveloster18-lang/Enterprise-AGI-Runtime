"""Ferramentas de desenvolvimento: o agente propõe, o Runtime governa.

`dev.propose` é o único caminho pelo qual um agente cria um artefato novo.
Não existe `dev.apply` exposto a agentes: promoção sem humano não existe no EGR
— a aprovação é um ato humano, e por isso ela não é uma ferramenta.
"""

from __future__ import annotations

from ...core.errors import ConfigError
from ...domain.enums import RiskLevel
from ...domain.proposal import ChangeProposal
from ...domain.tool import ToolRequest, ToolResult, ToolSpec
from ..protocol import Tool, ToolContext

KINDS = ("agent", "tool", "workflow", "policy")


class DevProposeTool(Tool):
    """Registra uma proposta de mudança. Nunca escreve no workspace."""

    spec = ToolSpec(
        name="dev.propose",
        description="Propõe um novo agente, ferramenta, workflow ou política (não aplica: exige aprovação humana)",
        parameters={
            "kind": {"type": "string", "required": True, "help": "agent | tool | workflow | policy"},
            "name": {"type": "string", "required": True, "help": "id do artefato (minúsculas, ponto, hífen)"},
            "content": {"type": "string", "required": True, "help": "YAML ou código Python completo"},
            "rationale": {"type": "string", "required": False, "help": "por que esta mudança é necessária"},
        },
        risk=RiskLevel.MEDIUM,
        side_effects=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        runtime = _runtime_from_ctx(ctx)
        if runtime is None:
            return ToolResult.failure("Runtime indisponível para registrar a proposta")

        kind = str(request.args.get("kind") or "").strip()
        name = str(request.args.get("name") or "").strip()
        content = request.args.get("content") or ""
        rationale = str(request.args.get("rationale") or "")
        origin = f"agent:{request.agent_id}" if request.agent_id else "human:cli"

        if kind not in KINDS:
            return ToolResult.failure(f"kind inválido: {kind} (use {', '.join(KINDS)})")

        try:
            proposal: ChangeProposal = runtime.workbench.propose(
                kind, name, content, origin=origin, rationale=rationale
            )
        except ConfigError as exc:
            return ToolResult.failure(str(exc))

        return ToolResult.success(
            {
                "proposal": proposal.id,
                "status": str(proposal.status),
                "target": proposal.target,
                "checks": len(proposal.checks),
                "errors": [check.detail for check in proposal.errors[:5]],
                "warnings": [check.detail for check in proposal.warnings[:5]],
                "next": "um humano precisa aprovar (egr dev approve) — agentes não aplicam mudanças",
            }
        )


class DevProposalsTool(Tool):
    """Lista propostas existentes (leitura)."""

    spec = ToolSpec(
        name="dev.proposals",
        description="Lista propostas de mudança e seus estados",
        parameters={
            "status": {"type": "string", "required": False},
            "limit": {"type": "integer", "required": False},
        },
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        runtime = _runtime_from_ctx(ctx)
        if runtime is None:
            return ToolResult.failure("Runtime indisponível")
        status = request.args.get("status")
        limit = int(request.args.get("limit") or 20)
        proposals = runtime.workbench.list(status=status, limit=limit)
        return ToolResult.success(
            {
                "count": len(proposals),
                "proposals": [proposal.summary() for proposal in proposals],
            }
        )


class DevTrialTool(Tool):
    """Prova uma ferramenta proposta dentro do sandbox."""

    spec = ToolSpec(
        name="dev.trial",
        description="Executa uma ferramenta proposta no sandbox (isolada, dry-run) e registra o resultado",
        parameters={
            "proposal": {"type": "string", "required": True, "help": "id da proposta"},
            "args": {"type": "object", "required": False, "help": "argumentos da execução"},
            "timeout": {"type": "integer", "required": False},
        },
        risk=RiskLevel.HIGH,
        side_effects=True,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        runtime = _runtime_from_ctx(ctx)
        if runtime is None:
            return ToolResult.failure("Runtime indisponível")
        proposal_id = str(request.args.get("proposal") or "")
        args = request.args.get("args") or {}
        timeout = int(request.args.get("timeout") or 10)
        if not isinstance(args, dict):
            return ToolResult.failure("'args' precisa ser um objeto")

        try:
            proposal = runtime.workbench.trial(proposal_id, args, timeout=timeout)
        except ConfigError as exc:
            return ToolResult.failure(str(exc))

        report = proposal.last_trial
        return ToolResult.success(
            {
                "proposal": proposal.id,
                "status": str(proposal.status),
                "ok": report.ok if report else False,
                "mode": report.mode if report else None,
                "duration_ms": report.duration_ms if report else 0,
                "output": report.output if report else None,
                "error": report.error if report else None,
                "files": report.files if report else [],
            }
        )


def _runtime_from_ctx(ctx: ToolContext):
    """O ToolContext carrega quem pode resolvê-lo: o próprio Runtime."""

    return getattr(ctx, "runtime", None)


__all__ = ["DevProposalsTool", "DevProposeTool", "DevTrialTool"]
