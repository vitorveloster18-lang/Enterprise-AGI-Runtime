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
from ..domain.release import Release, ReleaseItem, rank
from ..security.rbac import RELEASE_PROMOTE, has_permission, role_satisfies
from .gates import evaluate
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

    def approve(
        self,
        release_id: str,
        actor: str = "human:cli",
        *,
        token: str | None = None,
        note: str = "",
    ) -> Release:
        """Aprovação humana. Produção exige papel mais alto que staging."""

        release = self.get(release_id)
        if release.status != ReleaseStatus.SUBMITTED:
            raise ConfigError(f"release {release.id} está {release.status}: nada a aprovar")

        decisor = self._authorize(release, actor, token)
        release.status = ReleaseStatus.APPROVED
        release.decided_by = decisor
        release.decided_at = utcnow()
        release.decision_note = note or None
        release.updated_at = utcnow()
        saved = self._save(release)
        self.runtime.audit.record(
            EventType.RELEASE_APPROVED,
            actor=decisor,
            environment=str(saved.target),
            payload={
                "release": saved.id,
                "itens": [item.key for item in saved.items],
                "note": note,
                "identidade_verificada": decisor != actor.replace("human:", ""),
            },
        )
        return saved

    def reject(self, release_id: str, actor: str = "human:cli", note: str = "") -> Release:
        release = self.get(release_id)
        if release.status in (ReleaseStatus.DEPLOYED, ReleaseStatus.ROLLED_BACK):
            raise ConfigError(f"release {release.id} está {release.status}: não dá mais para recusar")

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
            payload={"release": saved.id, "note": note},
        )
        return saved

    def deploy(self, release_id: str, actor: str = "human:cli") -> Release:
        """Aplica o release: escreve o conteúdo versionado e avança o ambiente."""

        release = self.check(release_id)
        if release.status != ReleaseStatus.APPROVED:
            raise ConfigError(f"release {release.id} está {release.status}: só aprovados são aplicados")
        if not release.clear:
            raise ConfigError("gates reprovaram depois da aprovação: reavalie antes de aplicar")

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
