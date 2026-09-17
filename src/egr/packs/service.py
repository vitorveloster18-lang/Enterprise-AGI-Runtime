"""PackService: catálogo, verificação, proposta e aplicação de packs verticais.

Ordem, sempre:

    catálogo → verificar (requisitos, colisões, ambiente) → propor
    → aprovar (humano) → aplicar (escreve + recarrega + registra)

O pack **não escreve nada** ao ser proposto: ele vira uma `ChangeProposal` de
tipo `pack`, com o manifesto como conteúdo. Aplicar é o único momento em que
arquivos aparecem no workspace — e é o Fase 7 quem aplica.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..core.errors import ConfigError
from ..core.timeutil import utcnow
from ..domain.enums import EventType, PackStatus, ProposalKind
from ..domain.pack import ARTIFACT_DIRS, ARTIFACT_KINDS, InstalledPack, Pack
from .catalog import available_packs, load_pack_dir

ENVIRONMENTS = ("development", "staging", "production")


class PackService:
    """O catálogo é do Runtime; a instalação é do humano."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    # ---- caminhos -----------------------------------------------------
    @property
    def workspace(self) -> Path:
        return Path(self.runtime.settings.workspace)

    @property
    def packs_dir(self) -> Path:
        return self.workspace / "packs"

    # ---- catálogo -------------------------------------------------------
    def available(self) -> list[Pack]:
        return available_packs(self.workspace)

    def get(self, pack_id: str) -> Pack:
        for pack in self.available():
            if pack.id == pack_id:
                return pack
        raise ConfigError(f"pack não encontrado: {pack_id}")

    def installed(self) -> list[InstalledPack]:
        return self.runtime.packs_repository.list()

    def is_installed(self, pack_id: str) -> bool:
        return self.runtime.packs_repository.get(pack_id) is not None

    # ---- verificação ------------------------------------------------------
    def check(self, pack: Pack) -> list[dict[str, Any]]:
        """Requisitos e colisões. Erro aqui significa: nem propor."""

        checks: list[dict[str, Any]] = []

        def add(name: str, ok: bool, level: str = "error", detail: str = "") -> None:
            # detalhe só aparece quando há algo a dizer: ok com "não está registrada"
            # leria como erro
            nivel = "info" if ok and level == "error" else level
            checks.append({"nome": name, "ok": bool(ok), "nível": nivel, "detalhe": detail if not ok else ""})

        runtime = self.runtime
        known_tools = {tool["name"] for tool in runtime.tools.list()}
        for tool in pack.requires.tools:
            add(f"ferramenta:{tool}", tool in known_tools, detail=f"ferramenta '{tool}' não está registrada")

        declared = {item.id for item in runtime.connectors.list()}
        for connector in pack.requires.connectors:
            add(
                f"conector:{connector}",
                connector in declared,
                detail=f"conector '{connector}' não está declarado (integrations/*.yaml)",
            )

        known_agents = set(runtime.agents)
        for agent in pack.requires.agents:
            add(f"agente:{agent}", agent in known_agents, detail=f"agente '{agent}' não existe")

        minimum = pack.requires.min_environment or "development"
        current = str(runtime.settings.environment)
        add(
            "ambiente",
            ENVIRONMENTS.index(current) >= ENVIRONMENTS.index(minimum),
            detail=f"ambiente mínimo '{minimum}' (atual: {current})",
            level="error",
        )

        add("conteúdo", pack.total > 0, detail="pack sem artefatos")

        for kind in ARTIFACT_KINDS:
            for item in getattr(pack, kind):
                item_id = str(item.get("id") or "")
                if not item_id:
                    add(f"{kind}:id", False, detail=f"{kind}: artefato sem id")
                    continue
                target = self._path_for(kind, item_id)
                if target.exists():
                    add(
                        f"{kind}:{item_id}",
                        True,
                        level="warning",
                        detail=f"{target.relative_to(self.workspace)} já existe (será sobrescrito)",
                    )

        required_by_id: set[str] = set()
        for policy in pack.policies:
            for rule in policy.get("rules", []) or []:
                if rule.get("required_role"):
                    required_by_id.add(str(rule["required_role"]))
        for role in sorted(required_by_id):
            add("papel:" + role, True, level="info", detail=f"política exige o papel '{role}'")

        return checks

    def plan(self, pack: Pack) -> list[str]:
        """Arquivos que a aplicação vai escrever (relativos ao workspace)."""

        files = [f"packs/{pack.id}.yaml"]
        for kind in ARTIFACT_KINDS:
            for item in getattr(pack, kind):
                item_id = str(item.get("id") or "")
                if item_id:
                    files.append(str(self._path_for(kind, item_id).relative_to(self.workspace)))
        for name in pack.documents:
            files.append(f"documents/{name}")
        return files

    # ---- instalação ------------------------------------------------------
    def propose(self, pack_id: str, *, actor: str = "human:cli", rationale: str = "") -> Any:
        """Cria a proposta. Não escreve nada: quem escreve é o apply da Fase 7."""

        pack = self.get(pack_id)
        failures = [item for item in self.check(pack) if not item["ok"] and item["nível"] == "error"]
        if failures:
            detail = failures[0]["detalhe"] or failures[0]["nome"]
            self.runtime.audit.record(
                EventType.PACK_DENIED,
                actor=actor,
                environment=self.runtime.settings.environment,
                payload={"pack": pack.id, "motivo": detail},
            )
            raise ConfigError(f"pack '{pack.id}' reprovado na verificação: {detail}")

        return self.runtime.propose_change(
            ProposalKind.PACK,
            pack.id,
            pack.to_yaml(),
            origin=actor,
            rationale=rationale or f"pack vertical {pack.title} {pack.version}",
        )

    def materialize(
        self,
        pack: Pack,
        *,
        actor: str = "human:cli",
        proposal: str | None = None,
        update: dict | None = None,
    ) -> InstalledPack:
        """Escreve os artefatos, recarrega o Runtime e registra a instalação.

        Com `update` (plano de atualização de um pack já instalado), o arquivo
        que foi **editado depois da instalação** é preservado por padrão —
        sobrescrever exige `--overwrite` na proposta, porque apagar o ajuste de
        alguém em silêncio é pior que conviver com uma versão antiga.
        """

        import yaml

        overwrite = set((update or {}).get("overwrite") or [])
        written: list[str] = []
        checksums: dict[str, str] = {}
        preserved: list[str] = []

        def may_write(relative: str) -> bool:
            if not update:
                return True
            target = self.workspace / relative
            if relative in overwrite:
                return True
            return not (target.exists() and relative in set(update.get("conflito") or []))

        for kind in ARTIFACT_KINDS:
            items = getattr(pack, kind)
            if not items:
                continue
            directory = self.workspace / ARTIFACT_DIRS[kind]
            directory.mkdir(parents=True, exist_ok=True)
            for item in items:
                item_id = str(item.get("id") or "")
                if not item_id:
                    continue
                target = self._path_for(kind, item_id)
                relative = str(target.relative_to(self.workspace))
                if not may_write(relative):
                    preserved.append(relative)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = {key: value for key, value in item.items() if key != "origin"}
                content = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default_flow_style=False)
                target.write_text(content, encoding="utf-8")
                written.append(relative)
                checksums[relative] = hashlib.sha256(content.encode()).hexdigest()[:16]

        manifest = self.packs_dir / f"{pack.id}.yaml"
        relative = f"packs/{pack.id}.yaml"
        if manifest.exists() and may_write(relative):  # escrito pela aplicação da proposta (Fase 7)
            written.append(relative)
            checksums[relative] = hashlib.sha256(manifest.read_text(encoding="utf-8").encode()).hexdigest()[:16]
        elif manifest.exists():
            preserved.append(relative)

        for name, content in pack.documents.items():
            relative = f"documents/{name}"
            if not may_write(relative):
                preserved.append(relative)
                continue
            target = self.workspace / "documents" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(relative)
            checksums[relative] = hashlib.sha256(content.encode()).hexdigest()[:16]

        previous = self.runtime.packs_repository.get(pack.id)
        if previous is not None:
            # arquivo preservado continua registrado com a impressão que tem hoje
            for relative in preserved:
                recorded = self._recorded_checksum(previous, relative)
                if recorded:
                    checksums[relative] = recorded
                written.append(relative) if relative not in written else None

        self._reload(pack)

        installed = InstalledPack(
            id=pack.id,
            version=pack.version,
            status=PackStatus.INSTALLED,
            checksum=pack.checksum(),
            source=pack.origin,
            files=sorted(written),
            checksums=checksums,
            installed_by=actor,
            proposal=proposal,
            installed_at=utcnow(),
        )
        self.runtime.packs_repository.save(installed)
        self.runtime.audit.record(
            EventType.PACK_UPDATED if previous is not None else EventType.PACK_INSTALLED,
            actor=actor,
            environment=self.runtime.settings.environment,
            payload={
                "pack": pack.id,
                "versão": pack.version,
                "de": previous.version if previous else "-",
                "impressão": installed.checksum,
                "arquivos": len(written),
                "preservados": preserved,
                "proposta": proposal or "-",
            },
        )
        return installed

    # ---- atualização (lacuna 12b) ----------------------------------------
    def update_plan(self, pack_id: str) -> dict[str, Any]:
        """Compara o instalado com o catálogo: o que entra, o que muda, o que conflita.

        Três situações para cada arquivo do pack:

        - `novo`: não existe no workspace — entra inteiro;
        - `atualizável`: existe **exatamente** como o pack escreveu — pode
          ser substituído sem perder trabalho de ninguém;
        - `conflito`: existe com conteúdo diferente do registrado (alguém
          editou) — só entra se a proposta disser `--overwrite`.
        """

        installed = self.runtime.packs_repository.get(pack_id)
        if installed is None:
            raise ConfigError(f"pack não instalado: {pack_id}")
        pack = self.get(pack_id)
        manifest = f"packs/{pack.id}.yaml"
        novo: list[str] = []
        atualizavel: list[str] = []
        conflito: list[str] = []
        for relative in self.plan(pack):
            target = self.workspace / relative
            recorded = self._recorded_checksum(installed, relative)
            if not target.exists():
                novo.append(relative)
                continue
            if relative == manifest:
                # o manifesto é o alvo da própria proposta: ele é a atualização
                atualizavel.append(relative)
                continue
            try:
                current = hashlib.sha256(target.read_text(encoding="utf-8").encode()).hexdigest()[:16]
            except (OSError, UnicodeDecodeError):
                conflito.append(relative)
                continue
            if recorded and current == recorded:
                atualizavel.append(relative)
            else:
                conflito.append(relative)
        return {
            "pack": pack.id,
            "de": installed.version,
            "para": pack.version,
            "novo": novo,
            "atualizável": atualizavel,
            "conflito": conflito,
            # mesma versão, nada novo e nada editado: não há atualização, há reescrita
            "idêntico": installed.version == pack.version and not novo and not conflito,
        }

    def update(
        self,
        pack_id: str,
        *,
        actor: str = "human:cli",
        rationale: str = "",
        overwrite: bool = False,
    ) -> Any:
        """Atualiza por proposta — e diz em voz alta o que foi editado por aqui."""

        plan = self.update_plan(pack_id)
        if plan["idêntico"]:
            raise ConfigError(f"pack '{pack_id}' já está na versão {plan['para']} e não há nada a atualizar")
        proposal = self.propose(
            pack_id,
            actor=actor,
            rationale=rationale or f"atualização {plan['de']} → {plan['para']}",
        )
        proposal.metadata["pack_update"] = {
            **plan,
            "overwrite": list(plan["conflito"]) if overwrite else [],
            "sobrescrever": bool(overwrite),
        }
        self.runtime.proposals.save(proposal)
        return proposal

    def remove(self, pack_id: str, *, actor: str = "human:cli") -> dict[str, Any]:
        """Remove o que o pack escreveu — e só o que não foi mexido depois."""

        installed = self.runtime.packs_repository.get(pack_id)
        if installed is None:
            raise ConfigError(f"pack não instalado: {pack_id}")

        removed: list[str] = []
        kept: list[str] = []
        for relative in installed.files:
            target = self.workspace / relative
            if not target.exists():
                continue
            current = hashlib.sha256(target.read_text(encoding="utf-8").encode()).hexdigest()[:16]
            recorded = self._recorded_checksum(installed, relative)
            if recorded and current != recorded:
                kept.append(relative)
                continue
            target.unlink()
            removed.append(relative)

        self.runtime.packs_repository.delete(pack_id)
        self._reload_all()
        self.runtime.audit.record(
            EventType.PACK_REMOVED,
            actor=actor,
            environment=self.runtime.settings.environment,
            payload={"pack": pack_id, "removidos": len(removed), "mantidos": kept},
        )
        return {"pack": pack_id, "removidos": removed, "mantidos": kept}

    # ---- estado ----------------------------------------------------------
    def status(self) -> dict[str, Any]:
        installed = {item.id: item for item in self.installed()}
        catalog = self.available()
        itens: list[dict[str, Any]] = []
        for pack in catalog:
            record = installed.get(pack.id)
            if record is None:
                situacao = PackStatus.AVAILABLE
            elif record.version != pack.version or record.checksum != pack.checksum():
                situacao = PackStatus.OUTDATED
            else:
                situacao = PackStatus.INSTALLED
            itens.append(
                {
                    "id": pack.id,
                    "nome": pack.title,
                    "versão": pack.version,
                    "vertical": pack.vertical or "-",
                    "origem": pack.origin,
                    "status": str(situacao),
                    "artefatos": pack.total,
                    "instalado": record.summary() if record else None,
                }
            )
        propostas = [
            item
            for item in self.runtime.proposals.list(limit=100)
            if str(item.kind) == ProposalKind.PACK and str(item.status) not in ("applied", "rejected")
        ]
        for proposta in propostas:
            if proposta.name not in {item["id"] for item in itens}:
                continue
            for item in itens:
                if item["id"] == proposta.name and item["status"] == str(PackStatus.AVAILABLE):
                    item["status"] = str(PackStatus.PROPOSED)
                    item["proposta"] = proposta.id
        return {
            "catálogo": {
                "total": len(catalog),
                "embutidos": sum(1 for pack in catalog if pack.origin == "builtin"),
                "do_workspace": sum(1 for pack in catalog if pack.origin == "workspace"),
            },
            "instalados": len(installed),
            "pacotes": itens,
            "diretório": str(self.packs_dir),
        }

    # ---- internos ---------------------------------------------------------
    def _path_for(self, kind: str, item_id: str) -> Path:
        return self.workspace / ARTIFACT_DIRS[kind] / f"{item_id}.yaml"

    @staticmethod
    def _recorded_checksum(installed: InstalledPack, relative: str) -> str | None:
        """Só remove arquivo idêntico ao que o pack escreveu."""

        return (installed.checksums or {}).get(relative)

    def _reload(self, pack: Pack) -> None:
        runtime = self.runtime
        if pack.agents:
            runtime.sync_agents()
            runtime.reload_agents()
        if pack.policies:
            runtime.sync_policies()
        if pack.workflows:
            runtime._load_workflows()
        if pack.integrations:
            runtime.sync_integrations()
        if pack.evaluations:
            runtime.evaluation_suites = runtime._load_suites()

    def _reload_all(self) -> None:
        runtime = self.runtime
        runtime.sync_agents()
        runtime.reload_agents()
        runtime.sync_policies()
        runtime.sync_integrations()
        runtime._load_workflows()
        runtime.evaluation_suites = runtime._load_suites()


def load_workspace_packs(workspace: Path) -> list[Pack]:
    return load_pack_dir(Path(workspace) / "packs", origin="workspace")


__all__ = ["ENVIRONMENTS", "PackService", "load_workspace_packs"]
