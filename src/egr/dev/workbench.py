"""Workbench — o ambiente de desenvolvimento do Runtime (Fase 7).

Ciclo de uma mudança:

    propor → verificar → provar (código) → aprovar → aplicar

Cada etapa é um estado persistido e um evento no ledger. Um agente pode
executar o primeiro passo (`dev.propose`); os outros pertencem ao Runtime e ao
humano. Nada disso é cosmético: `apply` é o único método que escreve no
workspace, e ele exige status `approved`.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from ..core.errors import ConfigError
from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.enums import Environment, EventType, ProposalKind, ProposalStatus, RiskLevel
from ..domain.proposal import ChangeProposal
from .harness import DEFAULT_TIMEOUT, run_trial
from .loader import load_tool_dir
from .scaffold import scaffold
from .validators import validate_proposal

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
KIND_RISK = {
    ProposalKind.TOOL: RiskLevel.HIGH,      # executa código
    ProposalKind.POLICY: RiskLevel.HIGH,    # muda o que é permitido
    ProposalKind.WORKFLOW: RiskLevel.MEDIUM,
    ProposalKind.AGENT: RiskLevel.MEDIUM,
}
VALIDATABLE = (ProposalStatus.DRAFT, ProposalStatus.VALIDATED, ProposalStatus.FAILED, ProposalStatus.TESTED)


class Workbench:
    """Onde agentes e humanos constroem, sob proposta, e o Runtime governa."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    # ---- caminhos ----------------------------------------------------
    @property
    def workspace(self) -> Path:
        return Path(self.runtime.settings.workspace)

    @property
    def dev_dir(self) -> Path:
        return self.workspace / ".egr" / "dev"

    @property
    def proposals_dir(self) -> Path:
        return self.dev_dir / "proposals"

    @property
    def history_dir(self) -> Path:
        return self.dev_dir / "history"

    @staticmethod
    def target_for(kind: ProposalKind | str, name: str) -> str:
        kind = str(kind)
        if kind == ProposalKind.AGENT:
            return f"agents/{name}.yaml"
        if kind == ProposalKind.WORKFLOW:
            return f"workflows/{name}.yaml"
        if kind == ProposalKind.POLICY:
            return f"policies/{name}.yaml"
        if kind == ProposalKind.TOOL:
            return f"tools/{name.split('.')[0]}.py"
        raise ConfigError(f"tipo de proposta desconhecido: {kind}")

    # ---- ciclo de vida ------------------------------------------------
    def propose(
        self,
        kind: str | ProposalKind,
        name: str,
        content: str,
        *,
        origin: str = "human:cli",
        rationale: str = "",
        environment: Environment | str = Environment.DEVELOPMENT,
        auto_validate: bool = True,
    ) -> ChangeProposal:
        """Registra uma proposta. Não escreve nada no workspace."""

        kind = ProposalKind(str(kind))
        self._check_name(name)
        if not content or not content.strip():
            raise ConfigError("proposta sem conteúdo")

        proposal = ChangeProposal(
            id=new_id("proposal"),
            kind=kind,
            name=name,
            target=self.target_for(kind, name),
            content=content,
            origin=origin,
            rationale=rationale,
            environment=Environment(str(environment)),
            risk=KIND_RISK[kind],
            requires_approval=True,
        )
        proposal.refresh_fingerprint()
        self._save(proposal)
        self._mirror(proposal)

        self.runtime.audit.record(
            EventType.DEV_PROPOSAL_CREATED,
            actor=origin,
            agent_id=proposal.created_by_agent,
            environment=proposal.environment,
            payload={
                "proposal": proposal.id,
                "kind": str(kind),
                "name": name,
                "target": proposal.target,
                "risk": str(proposal.risk),
                "rationale": rationale[:300],
                "chars": len(content),
            },
        )
        if auto_validate:
            proposal = self.validate(proposal.id)
        return proposal

    def validate(self, proposal_id: str) -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal.status in (ProposalStatus.APPLIED, ProposalStatus.REJECTED):
            raise ConfigError(f"proposta {proposal_id} já está {proposal.status}")

        proposal.checks = validate_proposal(proposal, self.runtime)
        proposal.error = None
        if proposal.errors:
            proposal.status = ProposalStatus.FAILED
            proposal.error = proposal.errors[0].detail
        else:
            proposal.status = ProposalStatus.VALIDATED
        proposal.updated_at = utcnow()
        saved = self._save(proposal)
        self._mirror(saved)

        self.runtime.audit.record(
            EventType.DEV_PROPOSAL_VALIDATED if saved.valid else EventType.DEV_PROPOSAL_FAILED,
            actor=saved.origin,
            agent_id=saved.created_by_agent,
            environment=saved.environment,
            payload={
                "proposal": saved.id,
                "status": str(saved.status),
                "checks": len(saved.checks),
                "errors": [check.detail for check in saved.errors[:5]],
                "warnings": [check.detail for check in saved.warnings[:5]],
            },
        )
        return saved

    def trial(
        self,
        proposal_id: str,
        args: dict | None = None,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        container: bool | None = None,
    ) -> ChangeProposal:
        """Prova a ferramenta no sandbox. Só faz sentido para código."""

        proposal = self.get(proposal_id)
        if proposal.kind != ProposalKind.TOOL:
            raise ConfigError(
                f"prova em sandbox vale para ferramentas; '{proposal.kind}' é avaliado por execução (Fase 8)"
            )
        if proposal.status not in VALIDATABLE:
            raise ConfigError(f"proposta {proposal.id} está {proposal.status}: Verifique antes de provar")

        use_container = self.runtime.sandbox_info.get("mode") == "container" if container is None else container
        report = run_trial(
            content=proposal.content,
            tool_name=proposal.name,
            args=args or {},
            timeout=timeout,
            agent_id=proposal.created_by_agent,
            container=use_container,
        )
        proposal.trials.append(report)
        proposal.error = None if report.ok else (report.error or "a prova falhou")
        proposal.status = ProposalStatus.TESTED if report.ok else ProposalStatus.FAILED
        proposal.updated_at = utcnow()
        saved = self._save(proposal)

        self.runtime.audit.record(
            EventType.DEV_PROPOSAL_TESTED,
            actor=saved.origin,
            agent_id=saved.created_by_agent,
            environment=saved.environment,
            payload={
                "proposal": saved.id,
                "ok": report.ok,
                "mode": report.mode,
                "duration_ms": report.duration_ms,
                "timed_out": report.timed_out,
                "files": report.files[:10],
                "error": (report.error or "")[:300],
            },
        )
        return saved

    def approve(self, proposal_id: str, actor: str = "human:cli", note: str = "") -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal.status not in (ProposalStatus.VALIDATED, ProposalStatus.TESTED):
            raise ConfigError(f"proposta {proposal.id} está {proposal.status}: nada a aprovar")
        if proposal.kind == ProposalKind.TOOL and not proposal.tested_ok:
            raise ConfigError("ferramenta sem prova em sandbox: rode `egr dev test` antes de aprovar")

        proposal.status = ProposalStatus.APPROVED
        proposal.decided_by = actor
        proposal.decided_at = utcnow()
        proposal.decision_note = note or None
        proposal.updated_at = utcnow()
        saved = self._save(proposal)

        self.runtime.audit.record(
            EventType.DEV_PROPOSAL_APPROVED,
            actor=actor,
            agent_id=saved.created_by_agent,
            environment=saved.environment,
            payload={"proposal": saved.id, "kind": str(saved.kind), "name": saved.name, "note": note[:300]},
        )
        return saved

    def reject(self, proposal_id: str, actor: str = "human:cli", note: str = "") -> ChangeProposal:
        proposal = self.get(proposal_id)
        if proposal.status == ProposalStatus.APPLIED:
            raise ConfigError(f"proposta {proposal.id} já aplicada: recusar não desfaz (use o histórico em .egr/dev)")

        proposal.status = ProposalStatus.REJECTED
        proposal.decided_by = actor
        proposal.decided_at = utcnow()
        proposal.decision_note = note or None
        proposal.updated_at = utcnow()
        saved = self._save(proposal)

        self.runtime.audit.record(
            EventType.DEV_PROPOSAL_REJECTED,
            actor=actor,
            agent_id=saved.created_by_agent,
            environment=saved.environment,
            payload={"proposal": saved.id, "note": note[:300]},
        )
        return saved

    def apply(self, proposal_id: str, actor: str = "human:cli") -> ChangeProposal:
        """Escreve a proposta aprovada no workspace e recarrega o Runtime."""

        proposal = self.get(proposal_id)
        if proposal.status != ProposalStatus.APPROVED:
            raise ConfigError(f"proposta {proposal.id} está {proposal.status}: só aprovadas podem ser aplicadas")
        if not proposal.content_matches_fingerprint:
            raise ConfigError("o conteúdo mudou depois da aprovação: a proposta perdeu a validade")

        target = self._resolve_target(proposal)
        backup = self._backup(proposal, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._with_lineage(proposal), encoding="utf-8")

        self._reload(proposal)
        proposal.status = ProposalStatus.APPLIED
        proposal.applied_at = utcnow()
        proposal.updated_at = utcnow()
        saved = self._save(proposal)

        self.runtime.audit.record(
            EventType.DEV_PROPOSAL_APPLIED,
            actor=actor,
            agent_id=saved.created_by_agent,
            environment=saved.environment,
            payload={
                "proposal": saved.id,
                "kind": str(saved.kind),
                "name": saved.name,
                "target": saved.target,
                "backup": backup,
                "risk": str(saved.risk),
            },
        )
        return saved

    # ---- consulta -----------------------------------------------------
    def get(self, proposal_id: str) -> ChangeProposal:
        proposal = self.runtime.proposals.get(proposal_id)
        if proposal is None:
            raise ConfigError(f"proposta não encontrada: {proposal_id}")
        return proposal

    def list(
        self,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[ChangeProposal]:
        return self.runtime.proposals.list(status=status, kind=kind, limit=limit)

    def diff(self, proposal_id: str) -> str:
        """O que muda no workspace se a proposta for aplicada."""

        proposal = self.get(proposal_id)
        target = self._resolve_target(proposal)
        before = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
        after = self._with_lineage(proposal).splitlines()
        return "\n".join(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{proposal.target}",
                tofile=f"b/{proposal.target}",
                lineterm="",
            )
        )

    def status(self) -> dict[str, Any]:
        proposals = self.runtime.proposals.list(limit=200)
        by_status: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for proposal in proposals:
            by_status[str(proposal.status)] = by_status.get(str(proposal.status), 0) + 1
            by_kind[str(proposal.kind)] = by_kind.get(str(proposal.kind), 0) + 1
        tools_dir = self.workspace / "tools"
        return {
            "dev_dir": str(self.dev_dir),
            "targets": {str(kind): self.target_for(kind, "<nome>") for kind in ProposalKind},
            "proposals": {
                "total": self.runtime.proposals.count(),
                "by_status": by_status,
                "by_kind": by_kind,
                "awaiting_approval": by_status.get(ProposalStatus.VALIDATED, 0)
                + by_status.get(ProposalStatus.TESTED, 0),
                "recent": [proposal.summary() for proposal in proposals[:5]],
            },
            "workspace_tools": {
                "dir": str(tools_dir),
                "files": sorted(path.stem for path in tools_dir.glob("*.py")) if tools_dir.exists() else [],
                "rejected": getattr(self.runtime, "tool_load_rejections", []),
            },
            "sandbox": {
                "mode": self.runtime.sandbox_info.get("mode"),
                "configured": self.runtime.sandbox_info.get("configured"),
                "image": self.runtime.sandbox_info.get("image"),
                "network": self.runtime.sandbox_info.get("network"),
            },
            "agents": len(self.runtime.agents),
            "workflows": len(self.runtime.workflows),
        }

    def scaffold(self, kind: str | ProposalKind, name: str, *, agent: str = "") -> str:
        self._check_name(name)
        return scaffold(kind, name, agent=agent)

    # ---- internos -----------------------------------------------------
    def _check_name(self, name: str) -> None:
        if not NAME_PATTERN.match(name or ""):
            raise ConfigError(
                f"nome inválido: {name!r} (use minúsculas, dígitos, ponto, hífen ou underscore)"
            )

    def _save(self, proposal: ChangeProposal) -> ChangeProposal:
        proposal.updated_at = utcnow()
        return self.runtime.proposals.save(proposal)

    def _mirror(self, proposal: ChangeProposal) -> Path:
        """Espelho legível da proposta (humanos e diff trabalham melhor em arquivo)."""

        directory = self.proposals_dir / proposal.id
        directory.mkdir(parents=True, exist_ok=True)
        extension = "py" if proposal.kind == ProposalKind.TOOL else "yaml"
        path = directory / f"{proposal.name}.{extension}"
        path.write_text(proposal.content, encoding="utf-8")
        meta = directory / "proposal.json"
        meta.write_text(
            _json_dumps(proposal.summary()),
            encoding="utf-8",
        )
        return path

    def _resolve_target(self, proposal: ChangeProposal) -> Path:
        from ..core.paths import ensure_inside

        return ensure_inside(self.workspace, Path(proposal.target))

    def _backup(self, proposal: ChangeProposal, target: Path) -> str | None:
        if not target.exists():
            return None
        self.history_dir.mkdir(parents=True, exist_ok=True)
        backup = self.history_dir / f"{proposal.id}__{target.name}"
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        return str(backup.relative_to(self.workspace))

    def _with_lineage(self, proposal: ChangeProposal) -> str:
        """Marca o arquivo com a origem — linhagem visível no próprio artefato."""

        stamp = utcnow().isoformat()
        lines = [
            "",
            "# egr:origin: " + proposal.origin,
            "# egr:proposal: " + proposal.id,
            "# egr:applied_at: " + stamp,
        ]
        return proposal.content.rstrip("\n") + "\n" + "\n".join(lines) + "\n"

    def _reload(self, proposal: ChangeProposal) -> None:
        runtime = self.runtime
        if proposal.kind == ProposalKind.AGENT:
            runtime.sync_agents()
            runtime.reload_agents()
        elif proposal.kind == ProposalKind.WORKFLOW:
            runtime._load_workflows()
        elif proposal.kind == ProposalKind.POLICY:
            runtime.sync_policies()
        elif proposal.kind == ProposalKind.TOOL:
            self._reload_tools(proposal)

    def _reload_tools(self, proposal: ChangeProposal) -> None:
        runtime = self.runtime
        tools_dir = self.workspace / "tools"
        report = load_tool_dir(tools_dir, audit=runtime.audit, environment=str(proposal.environment))
        runtime.tool_load_rejections = [
            {**item, "file": item["file"]} for item in report.rejected
        ]
        for tool in report.loaded:
            runtime.tools.register(tool)
        runtime.audit.record(
            EventType.DEV_TOOL_LOADED,
            actor="runtime",
            environment=proposal.environment,
            payload={"loaded": report.names, "rejected": [item["file"] for item in report.rejected]},
        )


def _json_dumps(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


__all__ = ["Workbench"]
