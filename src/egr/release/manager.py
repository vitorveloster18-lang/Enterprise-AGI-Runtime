"""Release Manager — o caminho dev → staging → produção (Fase 9).

Nada chega a produção sozinho:

    create (snapshot + gates) → submit → approve (humano) → deploy

e, quando necessário, `rollback` restaura a revisão anterior de cada item.

O manager não decide *se* o artefato é bom — ele confere a evidência que as
Fases 7 e 8 produziram (proposta aprovada, avaliação aprovada, varredura limpa) e
entrega ao humano uma decisão com o contexto completo.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import AuthenticationError, AuthorizationError, ConfigError
from ..core.ids import new_id
from ..core.timeutil import utcnow
from ..domain.enums import Environment, EventType, ReleaseStatus
from ..domain.release import Release, ReleaseApproval, ReleaseItem, rank
from ..security.rbac import RELEASE_PROMOTE, has_permission, role_satisfies
from .gates import evaluate
from .signature import manifest_hash
from .versions import VersionStore

ENVIRONMENTS = ("staging", "production")


class ReleaseManager:
    """Promoção entre ambientes com versão, evidência e volta."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.versions = VersionStore(runtime)

    # ---- ciclo --------------------------------------------------------
    def create(
        self,
        items: list[tuple[str, str]],
        *,
        target: str = "staging",
        title: str = "",
        reason: str = "",
        created_by: str = "cli",
    ) -> Release:
        """Monta um release: tira snapshot dos itens e roda os gates."""

        try:
            target = Environment(str(target))
        except ValueError as exc:
            raise ConfigError(
                f"ambiente de destino inválido: {target} (use {', '.join(ENVIRONMENTS)})"
            ) from exc
        if str(target) not in ENVIRONMENTS:
            raise ConfigError(f"ambiente de destino inválido: {target} (use {', '.join(ENVIRONMENTS)})")
        if not items:
            raise ConfigError("release sem itens")

        resolved: list[ReleaseItem] = []
        for kind, name in items:
            if kind not in ("agent", "tool", "workflow", "policy"):
                raise ConfigError(f"tipo inválido: {kind}")
            try:
                snapshot = self.versions.snapshot(kind, name, actor=created_by, note=title or "release")
            except ConfigError:
                # artefato inexistente não derruba o release: vira impedimento declarado
                snapshot = None
            resolved.append(
                ReleaseItem(
                    kind=kind,
                    name=name,
                    version=snapshot.version if snapshot else "",
                    revision=snapshot.revision if snapshot else 0,
                    fingerprint=snapshot.fingerprint if snapshot else "",
                    from_environment=Environment(
                        _env_name(rank(_declared_environment(snapshot.content, kind))) if snapshot else "development"
                    ),
                    to_environment=target,
                )
            )

        release = Release(
            id=new_id("release"),
            title=title or f"{len(resolved)} artefato(s) → {target}",
            reason=reason,
            items=resolved,
            target=target,
            created_by=created_by,
        )
        # liga os snapshots ao release depois que ele tem id
        for item in resolved:
            if not item.revision:
                continue
            snapshot = self.runtime.versions.get(f"{item.kind}:{item.name}@{item.revision}")
            if snapshot is not None:
                snapshot.release_id = release.id
                self.runtime.versions.save(snapshot)

        release.checks = evaluate(self.runtime, release)
        release.evidence = self._evidence_ids(release)
        saved = self._save(release)
        self.runtime.audit.record(
            EventType.RELEASE_CREATED,
            actor=created_by,
            environment=str(target),
            payload={
                "release": saved.id,
                "target": str(target),
                "itens": [item.key for item in saved.items],
                "gates": len(saved.checks),
                "erros": len(saved.errors),
                "evidência": saved.evidence,
            },
        )
        return saved

    def check(self, release_id: str) -> Release:
        release = self.get(release_id)
        release.checks = evaluate(self.runtime, release)
        release.evidence = self._evidence_ids(release)
        saved = self._save(release)
        self.runtime.audit.record(
            EventType.RELEASE_SUBMITTED if saved.clear else EventType.RELEASE_CREATED,
            actor=saved.created_by,
            environment=str(saved.target),
            payload={
                "release": saved.id,
                "action": "gates",
                "erros": len(saved.errors),
                "avisos": len(saved.warnings),
            },
        )
        return saved

    def submit(self, release_id: str, actor: str = "cli") -> Release:
        release = self.check(release_id)
        if release.status not in (ReleaseStatus.DRAFT, ReleaseStatus.FAILED):
            raise ConfigError(f"release {release.id} está {release.status}: nada a submeter")
        if not release.clear:
            raise ConfigError(
                "gates reprovaram: " + "; ".join(check.detail for check in release.errors[:3])
            )

        release.status = ReleaseStatus.SUBMITTED
        release.updated_at = utcnow()
        saved = self._save(release)
        self.runtime.audit.record(
            EventType.RELEASE_SUBMITTED,
            actor=actor,
            environment=str(saved.target),
            payload={
                "release": saved.id,
                "itens": [item.key for item in saved.items],
                "evidência": saved.evidence,
            },
        )
        return saved

    # ---- lacuna 9b: quórum --------------------------------------------
    def quorum(self, release: Release) -> dict[str, Any]:
        """Quantos votos faltam — e quem já votou."""

        policy = self.runtime.settings.config.release
        required = policy.min_approvals_production if release.production else policy.min_approvals
        # quórum de um: quem criou pode aprovar (sempre foi humano decidindo).
        # quórum de mais de um: o autor não conta — dois votos exigem duas pessoas.
        exclude_author = required > 1 and not policy.allow_self_approval
        counted = release.approvers(exclude_author=exclude_author)
        return {
            "release": release.id,
            "destino": str(release.target),
            "exigido": required,
            "obtido": len(counted),
            "aprovadores": counted,
            "vetos": [item.actor for item in release.vetoes],
            "completo": len(counted) >= required and not release.vetoes,
            "criado_por": release.created_by,
        }

    def approvals(self, release_id: str) -> list[Release]:
        raise NotImplementedError

    def approve(
        self,
        release_id: str,
        actor: str = "human:cli",
        *,
        token: str | None = None,
        note: str = "",
    ) -> Release:
        """Voto na promoção. Com quórum, um voto não basta — e é isso o bom.

        O voto é registrado com nome e papéis de quem votou. Enquanto o quórum
        não fecha, o release continua `submitted`: ninguém aplica no escuro.
        """

        release = self.get(release_id)
        if release.status != ReleaseStatus.SUBMITTED:
            raise ConfigError(f"release {release.id} está {release.status}: nada a aprovar")

        decisor = self._authorize(release, actor, token)
        policy = self.runtime.settings.config.release

        voter = decisor.replace("human:", "") or decisor
        already = next((item for item in release.approvals if item.actor == voter), None)
        if already is not None:
            raise ConfigError(f"'{decisor}' já votou neste release ({already.decision})")

        principal = self.runtime.identity.resolve(token) if token else self.runtime.identity.resolve(
            decisor.replace("human:", "")
        )
        roles = sorted(getattr(principal, "roles", []) or [])
        if policy.approver_roles and roles and not set(roles) & set(policy.approver_roles):
            raise AuthorizationError(
                f"'{decisor}' tem papéis {roles}, mas votar promoção exige um de {sorted(policy.approver_roles)}"
            )

        release.approvals.append(
            ReleaseApproval(release_id=release.id, actor=voter, decision="approved", roles=roles, note=note)
        )
        release.updated_at = utcnow()
        self.runtime.audit.record(
            EventType.RELEASE_APPROVAL_RECORDED,
            actor=decisor,
            environment=str(release.target),
            payload={
                "release": release.id,
                "decisão": "approved",
                "papéis": roles,
                "quórum": self.quorum(release),
            },
        )

        state = self.quorum(release)
        if not state["completo"]:
            saved = self._save(release)
            saved.metadata["quórum"] = state
            return self._save(saved)

        release.metadata["manifesto_aprovado"] = manifest_hash(release)
        release.status = ReleaseStatus.APPROVED
        release.decided_by = ", ".join(state["aprovadores"])
        release.decided_at = utcnow()
        release.decision_note = note or None
        saved = self._save(release)
        saved.metadata["quórum"] = state
        saved = self._save(saved)
        self.runtime.audit.record(
            EventType.RELEASE_APPROVED,
            actor=decisor,
            environment=str(saved.target),
            payload={
                "release": saved.id,
                "itens": [item.key for item in saved.items],
                "note": note,
                "aprovadores": state["aprovadores"],
                "quórum": state["exigido"],
                "identidade_verificada": decisor != actor.replace("human:", ""),
            },
        )
        return saved

    # ---- lacuna 9b: assinatura -----------------------------------------
    def sign(self, release_id: str, *, actor: str = "human:cli") -> Release:
        """Assina o manifesto do release (conteúdo, não intenção)."""

        from .signature import sign as sign_release

        release = self.get(release_id)
        if release.status in (ReleaseStatus.DRAFT, ReleaseStatus.REJECTED, ReleaseStatus.ROLLED_BACK):
            raise ConfigError(f"release {release.id} está {release.status}: nada a assinar")
        sign_release(self.runtime, release, actor=actor)
        release.updated_at = utcnow()
        return self._save(release)

    def verify(self, release_id: str) -> dict[str, Any]:
        from .signature import verify as verify_release

        release = self.get(release_id)
        valid, detail = verify_release(self.runtime, release)
        return {
            "release": release.id,
            "válida": valid,
            "detalhe": detail,
            "assinatura": release.signature.summary() if release.signature else None,
            "quórum": self.quorum(release),
        }

    def reject(self, release_id: str, actor: str = "human:cli", note: str = "") -> Release:
        release = self.get(release_id)
        if release.status in (ReleaseStatus.DEPLOYED, ReleaseStatus.ROLLED_BACK):
            raise ConfigError(f"release {release.id} está {release.status}: não dá mais para recusar")

        # lacuna 9b: recusa é veto e fica registrada no histórico de votos
        name = actor.replace("human:", "")
        release.approvals.append(
            ReleaseApproval(release_id=release.id, actor=name, decision="rejected", note=note)
        )
        release.status = ReleaseStatus.REJECTED
        release.decided_by = actor
        release.decided_at = utcnow()
        release.decision_note = note or None
        release.updated_at = utcnow()
        saved = self._save(release)
        self.runtime.audit.record(
            EventType.RELEASE_REJECTED,
            actor=actor,
            environment=str(saved.target),
            payload={"release": saved.id, "note": note, "vetos": len(saved.vetoes)},
        )
        return saved

    def deploy(self, release_id: str, actor: str = "human:cli") -> Release:
        """Aplica o release: escreve o conteúdo versionado e avança o ambiente."""

        release = self.check(release_id)
        if release.status != ReleaseStatus.APPROVED:
            raise ConfigError(f"release {release.id} está {release.status}: só aprovados são aplicados")
        if not release.clear:
            raise ConfigError("gates reprovaram depois da aprovação: reavalie antes de aplicar")

        self._verify_promotion(release)

        target = str(release.target)
        for item in release.items:
            snapshot = self.runtime.versions.get(f"{item.kind}:{item.name}@{item.revision}")
            if snapshot is None:
                raise ConfigError(f"snapshot {item.kind}:{item.name}@{item.revision} não encontrado")
            # promoção aplica o conteúdo exato do snapshot, não "o que está no disco"
            path = self.versions.path_for(item.kind, item.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(snapshot.content, encoding="utf-8")
            self.versions.set_environment(item.kind, item.name, target, actor=actor)
            self.versions.snapshot(
                item.kind, item.name, actor=actor, note=f"aplicado em {target}", release_id=release.id
            )

        release.status = ReleaseStatus.DEPLOYED
        release.deployed_at = utcnow()
        release.updated_at = utcnow()
        saved = self._save(release)
        self.runtime.audit.record(
            EventType.RELEASE_DEPLOYED,
            actor=actor,
            environment=target,
            payload={
                "release": saved.id,
                "itens": [f"{item.key}@{item.version}" for item in saved.items],
                "evidência": saved.evidence,
            },
        )
        return saved

    def _verify_promotion(self, release: Release) -> None:
        """Lacuna 9b: só aplica o que foi assinado — e o que foi aprovado.

        Duas barreiras, ambas sobre conteúdo:
        1. ambientes exigidos têm que ter assinatura válida;
        2. o manifesto não pode ter mudado depois do voto (aprovar uma coisa e
           aplicar outra é o clássico acidente de governança).
        """

        from .signature import verify as verify_release

        approved = release.metadata.get("manifesto_aprovado")
        if approved and approved != manifest_hash(release):
            raise ConfigError(
                f"release {release.id} mudou depois da aprovação: o manifesto aprovado era "
                f"{approved[:12]} e agora é {manifest_hash(release)[:12]} — reavalie e aprove de novo"
            )
        required = [str(item) for item in self.runtime.settings.config.release.signature_environments]
        if str(release.target) not in required:
            return
        valid, detail = verify_release(self.runtime, release)
        if not valid:
            raise ConfigError(
                f"promoção para {release.target} exige assinatura válida: {detail} "
                f"(rode `egr release sign {release.id}`)"
            )

    def rollback(self, release_id: str, actor: str = "human:cli", note: str = "") -> Release:
        """Volta cada item para a revisão anterior à deste release."""

        original = self.get(release_id)
        if original.status != ReleaseStatus.DEPLOYED:
            raise ConfigError(f"release {original.id} está {original.status}: só aplicados podem ser revertidos")

        release = Release(
            id=new_id("release"),
            title=f"Rollback de {original.id}",
            reason=note or f"reversão de {original.id}",
            items=original.items,
            target=original.target,
            status=ReleaseStatus.ROLLED_BACK,
            rollback_of=original.id,
            created_by=actor,
            created_at=utcnow(),
        )

        for item in original.items:
            # o release versionou o estado anterior a ele mesmo: voltar é restaurar essa revisão
            captured = next(
                (
                    version
                    for version in self.versions.versions(item.kind, item.name)
                    if version.revision == item.revision
                ),
                None,
            )
            if captured is None:
                raise ConfigError(
                    f"snapshot r{item.revision} de {item.key} não está mais disponível: rollback impossível"
                )
            self.versions.restore(item.kind, item.name, captured.revision, actor=actor, note=release.reason)
            self.versions.set_environment(item.kind, item.name, str(item.from_environment), actor=actor)
            item.version = captured.version
            item.revision = captured.revision

        release.deployed_at = utcnow()
        release.rolled_back_at = utcnow()
        release.decided_by = actor
        release.decided_at = utcnow()
        saved = self._save(release)

        original.status = ReleaseStatus.ROLLED_BACK
        original.rolled_back_at = utcnow()
        self._save(original)

        self.runtime.audit.record(
            EventType.RELEASE_ROLLED_BACK,
            actor=actor,
            environment=str(saved.target),
            payload={
                "release": original.id,
                "reversão": saved.id,
                "itens": [f"{item.key}@{item.version}" for item in saved.items],
                "note": note,
            },
        )
        return saved

    # ---- consulta -----------------------------------------------------
    def get(self, release_id: str) -> Release:
        release = self.runtime.releases.get(release_id)
        if release is None:
            raise ConfigError(f"release não encontrado: {release_id}")
        return release

    def list(self, status: str | None = None, limit: int = 20) -> list[Release]:
        return self.runtime.releases.list(status=status, limit=limit)

    def status(self) -> dict[str, Any]:
        releases = self.runtime.releases.list(limit=100)
        by_status: dict[str, int] = {}
        for release in releases:
            by_status[str(release.status)] = by_status.get(str(release.status), 0) + 1
        deployed = [release for release in releases if release.status == ReleaseStatus.DEPLOYED]
        return {
            "ladder": ["development", "staging", "production"],
            "releases": {
                "total": self.runtime.releases.count(),
                "by_status": by_status,
                "recent": [release.summary() for release in releases[:5]],
            },
            "versions": {
                "total": self.runtime.versions.count(),
                "artifacts": sorted(
                    {
                        f"{version.kind}:{version.name}"
                        for version in self.runtime.versions.list(limit=200)
                    }
                ),
            },
            "deployed": {
                str(release.target): [
                    f"{item.key}@{item.version}" for release in deployed for item in release.items
                ]
                for release in deployed
            },
            "governance": {
                "identity_required": self.runtime.settings.config.security.identity_required,
                "approval_min_role": self.runtime.settings.config.security.approval_min_role,
                "allow_agent_approval": self.runtime.settings.config.security.allow_agent_approval,
            },
        }

    # ---- internos -----------------------------------------------------
    def _save(self, release: Release) -> Release:
        release.updated_at = utcnow()
        return self.runtime.releases.save(release)

    def _environment(self, value: Environment | str) -> Environment:
        return Environment(str(value))

    def _evidence_ids(self, release: Release) -> list[str]:
        ids: list[str] = []
        for item in release.items:
            for run in self.runtime.evaluations.list(target=item.name, limit=20):
                if str(run.target_kind) == item.kind and str(run.status) == "passed":
                    if run.id not in ids:
                        ids.append(run.id)
                    break
        return ids

    def _authorize(self, release: Release, actor: str, token: str | None) -> str:
        """Quem pode aprovar — a mesma régua de qualquer decisão no Runtime."""

        security = self.runtime.settings.config.security
        principal = (
            self.runtime.identity.resolve(token)
            if token
            else self.runtime.identity.resolve(actor.replace("human:", ""))
        )

        if not security.identity_required:
            return principal.id if principal else (actor or "human")

        if principal is None:
            raise AuthenticationError(
                f"identidade não verificada: aprovar release para {release.target} exige um principal "
                "autenticado (`egr identity token <id>` e --token)"
            )
        if not has_permission(principal, RELEASE_PROMOTE):
            raise AuthorizationError(f"'{principal.id}' não tem a permissão '{RELEASE_PROMOTE}'")
        if not security.allow_agent_approval and str(getattr(principal, "kind", "")) == "agent":
            raise AuthorizationError(f"agente '{principal.id}' não pode aprovar promoção")

        required = "operator" if str(release.target) == "staging" else security.approval_min_role
        if not role_satisfies(principal.roles, required):
            raise AuthorizationError(
                f"'{principal.id}' tem papéis {sorted(principal.roles)}, "
                f"mas promover para {release.target} exige '{required}'"
            )
        return principal.id


def _declared_environment(content: str, kind: str) -> str:
    from .versions import _declared_environment as parse

    return parse(kind, content)


def _env_name(rank_value: int) -> str:
    return {0: "development", 1: "staging", 2: "production"}.get(rank_value, "development")


__all__ = ["ReleaseManager"]
