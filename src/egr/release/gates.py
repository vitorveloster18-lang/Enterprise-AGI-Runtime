"""Gates de promoção: o que precisa ser verdade antes de um humano decidir.

Promover sem evidência é chutar com data marcada. Os gates conferem, por item:

1. o artefato existe;
2. há **avaliação aprovada** — e ela é da versão que está sendo promovida;
3. a varredura de segurança não tem achado crítico;
4. o degrau é o seguinte da escada (ninguém pula staging).

E por release: produção exige que o mesmo item já tenha sido **aplicado** em
staging. Gate que falha é erro declarado, não exceção escondida.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import EvaluationStatus
from ..domain.proposal import ValidationCheck
from ..domain.release import Release, rank
from ..evaluation.security import scan


def _check(name: str, ok: bool, level: str = "error", detail: str = "") -> ValidationCheck:
    return ValidationCheck(name=name, ok=ok, level=level if not ok else "info", detail=detail)


def evaluate(runtime: Any, release: Release) -> list[ValidationCheck]:
    """Confere um release. Não executa nada além de leitura e varredura."""

    checks: list[ValidationCheck] = [
        _check("itens", bool(release.items), detail="release sem itens")
    ]
    if not release.items:
        return checks

    target = str(release.target)
    for item in release.items:
        label = item.key
        origin = str(item.from_environment)
        if rank(target) <= rank(origin):
            checks.append(
                _check("escada", False, detail=f"{label}: '{origin}' → '{target}' não sobe de ambiente")
            )
        if rank(target) - rank(origin) > 1:
            checks.append(
                _check(
                    "escada",
                    False,
                    detail=f"{label}: pulou {rank(target) - rank(origin) - 1} degrau(s) entre '{origin}' e '{target}'",
                )
            )
        if target == "production" and not _deployed_to(runtime, item.kind, item.name, "staging"):
            checks.append(
                _check(
                    "estágio",
                    False,
                    detail=f"{label}: produção exige um release aplicado em staging antes",
                )
            )

        if not _exists(runtime, item.kind, item.name):
            checks.append(_check("artefato", False, detail=f"{label}: não existe no workspace"))
            continue

        checks.extend(_evidence(runtime, item.kind, item.name))

        findings = [finding for finding in scan(runtime, item.kind, item.name) if finding.blocking]
        if findings:
            for finding in findings[:3]:
                checks.append(
                    _check("segurança", False, detail=f"{label}: {finding.code} — {finding.detail}")
                )
    return checks


def _exists(runtime: Any, kind: str, name: str) -> bool:
    if kind == "agent":
        return name in runtime.agents
    if kind == "workflow":
        return name in runtime.workflows
    if kind == "policy":
        return any(policy.id == name for policy in runtime.policy.list_policies())
    if kind == "tool":
        return runtime.tools.has(name)
    return False


def _evidence(runtime: Any, kind: str, name: str) -> list[ValidationCheck]:
    """Avaliação aprovada e da versão certa — ou não é evidência."""

    runs = [
        run
        for run in runtime.evaluations.list(limit=100)
        if run.target == name and str(run.target_kind) == kind
    ]
    if not runs:
        return [
            _check(
                "evidência",
                False,
                detail=f"{kind}:{name}: nenhuma avaliação registrada (egr eval smoke|run)",
            )
        ]

    latest = runs[0]
    if str(latest.status) != EvaluationStatus.PASSED:
        return [
            _check(
                "evidência",
                False,
                detail=f"{kind}:{name}: última avaliação ({latest.id}) terminou em '{latest.status}'",
            )
        ]

    checks = [
        _check(
            "evidência",
            True,
            detail=f"{kind}:{name}: {latest.id} ({latest.suite_id}) acerto {latest.pass_rate:.0%}",
        )
    ]
    current = _declared_version(runtime, kind, name)
    if current and latest.artifact_version and latest.artifact_version != current:
        checks.append(
            _check(
                "evidência",
                False,
                level="warning",
                detail=(
                    f"{kind}:{name}: a avaliação é da versão {latest.artifact_version}, "
                    f"o artefato atual é {current} — reavalie antes de promover"
                ),
            )
        )
    return checks


def _declared_version(runtime: Any, kind: str, name: str) -> str:
    if kind == "agent" and name in runtime.agents:
        return runtime.agents[name].version
    if kind == "workflow" and name in runtime.workflows:
        return runtime.workflows[name].version
    if kind == "policy":
        policy = next((item for item in runtime.policy.list_policies() if item.id == name), None)
        return policy.version if policy else ""
    return ""


def _deployed_to(runtime: Any, kind: str, name: str, environment: str) -> bool:
    for release in runtime.releases.list(limit=100):
        if str(release.status) != "deployed" or str(release.target) != environment:
            continue
        if any(item.kind == kind and item.name == name for item in release.items):
            return True
    return False


__all__ = ["evaluate"]
