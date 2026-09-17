"""Baseline policy set shipped with the Runtime.

Principle: DEFAULT DENY. Everything that is not explicitly allowed is denied;
side-effecting and external-boundary actions require human approval outside of
Development.
"""

from __future__ import annotations

from ..domain.enums import DecisionType, Environment
from ..domain.policy import Policy, PolicyRule

DEV = Environment.DEVELOPMENT.value
STG = Environment.STAGING.value
PRD = Environment.PRODUCTION.value


def baseline_policy() -> Policy:
    return Policy(
        id="builtin-baseline",
        name="Baseline runtime policy",
        description="Default deny; dev staging production regime differences.",
        priority=0,
        builtin=True,
        rules=[
            # ---------------- external boundary ----------------
            PolicyRule(
                id="http-forbidden",
                action="http.request",
                condition="external_ai == 'forbidden'",
                decision=DecisionType.DENY,
                reason="enterprise policy forbids sending data to external AI/services",
            ),
            PolicyRule(
                id="http-restricted",
                action="http.request",
                condition="external_ai == 'restricted'",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="security_officer",
                reason="external calls are restricted and need a security officer",
            ),
            PolicyRule(
                id="http-production",
                action="http.request",
                condition=f"environment == '{PRD}'",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="external calls in production require approval",
            ),
            PolicyRule(
                id="http-allowed",
                action="http.request",
                decision=DecisionType.ALLOW,
                reason="external calls allowed outside production",
            ),
            # ---------------- code execution -------------------
            PolicyRule(
                id="python-exec-disabled",
                action="python.execute",
                condition="not python_exec_enabled",
                decision=DecisionType.DENY,
                reason="python execution is disabled in this workspace",
            ),
            PolicyRule(
                id="python-exec-production",
                action="python.execute",
                condition=f"environment == '{PRD}'",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="code execution in production requires approval",
            ),
            PolicyRule(
                id="python-exec-staging",
                action="python.execute",
                condition=f"environment == '{STG}'",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="code execution in staging requires approval",
            ),
            PolicyRule(
                id="python-exec-development",
                action="python.execute",
                condition=f"environment == '{DEV}'",
                decision=DecisionType.ALLOW,
                reason="sandboxed code execution is allowed in development",
            ),
            # ---------------- process execution ----------------
            PolicyRule(
                id="process-run",
                action="process.run",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="spawning processes always requires approval",
            ),
            # ---------------- filesystem -----------------------
            PolicyRule(
                id="filesystem-write-production",
                action="filesystem.write",
                condition=f"environment == '{PRD}'",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="writes in production require approval",
            ),
            PolicyRule(
                id="filesystem-write",
                action="filesystem.write",
                decision=DecisionType.ALLOW,
                reason="writes are allowed in development/staging (inside the workspace)",
            ),
            PolicyRule(
                id="filesystem-read",
                action="filesystem.read",
                decision=DecisionType.ALLOW,
                reason="reads are allowed inside the workspace",
            ),
            PolicyRule(
                id="filesystem-list",
                action="filesystem.list",
                decision=DecisionType.ALLOW,
                reason="listing is allowed inside the workspace",
            ),
            # ---------------- data -----------------------------
            PolicyRule(
                id="database-query-production",
                action="database.query",
                condition=f"environment == '{PRD}'",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="data_steward",
                reason="queries in production require approval",
            ),
            PolicyRule(
                id="database-query",
                action="database.query",
                decision=DecisionType.ALLOW,
                reason="read-only queries are allowed",
            ),
            # ---------------- integrations (Fase 11) -----------
            PolicyRule(
                id="integration-write-production",
                action="integration.call",
                condition=f"write == True and environment == '{PRD}'",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="writing to an external system from production requires approval",
            ),
            PolicyRule(
                id="integration-write",
                action="integration.call",
                condition="write == True",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="write calls to external systems require approval",
            ),
            PolicyRule(
                id="integration-read",
                action="integration.call",
                decision=DecisionType.ALLOW,
                reason="read-only calls to declared connectors are allowed",
            ),
            PolicyRule(
                id="integration-receive",
                action="integration.receive",
                decision=DecisionType.ALLOW,
                reason="inbound webhooks are recorded, not executed",
            ),
            # ---------------- git (Fase 3) ---------------------
            PolicyRule(
                id="git-read",
                action="git.status",
                decision=DecisionType.ALLOW,
                reason="leitura do repositório é livre",
            ),
            PolicyRule(
                id="git-diff",
                action="git.diff",
                decision=DecisionType.ALLOW,
                reason="diff é somente leitura",
            ),
            PolicyRule(
                id="git-log",
                action="git.log",
                decision=DecisionType.ALLOW,
                reason="histórico é somente leitura",
            ),
            PolicyRule(
                id="git-commit",
                action="git.commit",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="commits exigem aprovação humana",
            ),
            # ---------------- email (Fase 3) -------------------
            PolicyRule(
                id="email-send-forbidden",
                action="email.send",
                condition="external_ai == 'forbidden'",
                decision=DecisionType.DENY,
                reason="política da empresa proíbe envio de dados para fora",
            ),
            PolicyRule(
                id="email-send",
                action="email.send",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="envio de e-mail sempre exige aprovação humana",
            ),
            PolicyRule(
                id="email-read-production",
                action="email.read",
                condition=f"environment == '{PRD}'",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="leitura de caixa postal em produção exige aprovação",
            ),
            PolicyRule(
                id="email-read",
                action="email.read",
                decision=DecisionType.ALLOW,
                reason="leitura de e-mail permitida fora de produção",
            ),
            # ---------------- browser (Fase 3) -----------------
            PolicyRule(
                id="browser-navigate",
                action="browser.navigate",
                condition=f"environment == '{DEV}'",
                decision=DecisionType.ALLOW,
                reason="navegação permitida em desenvolvimento",
            ),
            PolicyRule(
                id="browser-navigate-guarded",
                action="browser.navigate",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="navegação fora de desenvolvimento exige aprovação",
            ),
            PolicyRule(
                id="browser-extract",
                action="browser.extract",
                condition=f"environment == '{DEV}'",
                decision=DecisionType.ALLOW,
                reason="extração permitida em desenvolvimento",
            ),
            PolicyRule(
                id="browser-extract-guarded",
                action="browser.extract",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="extração fora de desenvolvimento exige aprovação",
            ),
            # ---------------- desenvolvimento (Fase 7) ---------
            PolicyRule(
                id="dev-propose",
                action="dev.propose",
                condition=f"environment == '{DEV}'",
                decision=DecisionType.ALLOW,
                reason="propor mudança é o caminho normal de trabalho em desenvolvimento",
            ),
            PolicyRule(
                id="dev-propose-guarded",
                action="dev.propose",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="propor mudança fora de desenvolvimento exige aprovação",
            ),
            PolicyRule(
                id="dev-proposals",
                action="dev.proposals",
                decision=DecisionType.ALLOW,
                reason="listar propostas é leitura",
            ),
            PolicyRule(
                id="dev-trial-development",
                action="dev.trial",
                condition=f"environment == '{DEV}'",
                decision=DecisionType.ALLOW,
                reason="provar código no sandbox (isolado, dry-run) é permitido em desenvolvimento",
            ),
            PolicyRule(
                id="dev-trial-guarded",
                action="dev.trial",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="executar código proposto fora de desenvolvimento exige aprovação",
            ),
            # ---------------- memory ---------------------------
            PolicyRule(
                id="memory-write",
                action="memory.write",
                decision=DecisionType.ALLOW,
                reason="agents may record operational memory",
            ),
            PolicyRule(
                id="memory-search",
                action="memory.search",
                decision=DecisionType.ALLOW,
                reason="agents may recall enterprise memory",
            ),
        ],
    )


def production_guard_policy() -> Policy:
    """Extra belt for production: high risk actions are never silent."""

    return Policy(
        id="builtin-production-guard",
        name="Production guard",
        description="High/critical risk actions in production always need a human.",
        priority=10,
        builtin=True,
        rules=[
            PolicyRule(
                id="high-risk-production",
                action="*",
                condition=f"environment == '{PRD}' and risk in ['high', 'critical']",
                decision=DecisionType.REQUIRE_APPROVAL,
                required_role="operator",
                reason="high risk action in production requires approval",
            )
        ],
    )


def default_policies() -> list[Policy]:
    return [production_guard_policy(), baseline_policy()]
