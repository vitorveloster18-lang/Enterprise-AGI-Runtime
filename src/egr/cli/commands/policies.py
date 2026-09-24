"""egr policy list|test|sync · autorização é do Runtime, nunca do prompt."""

from __future__ import annotations

from pathlib import Path

import typer

from ..context import get_runtime
from ..formatting import json_output, kv, success, table

app = typer.Typer(help="Políticas: quem pode fazer o quê, onde e quando")


@app.command(name="list")
def list_policies(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    rules: bool = typer.Option(False, "--rules", "-r", help="Mostrar as regras"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista as políticas ativas."""

    runtime = get_runtime(workspace)
    policies = runtime.policy.list_policies()
    if as_json:
        json_output([policy.model_dump(mode="json") for policy in policies])
        return
    table(
        "Políticas",
        ["id", "prioridade", "ativa", "embutida", "regras"],
        [
            [
                policy.id,
                policy.priority,
                "sim" if policy.enabled else "não",
                "sim" if policy.builtin else "não",
                len(policy.rules),
            ]
            for policy in policies
        ],
    )
    if rules:
        rows = []
        for policy in policies:
            for rule in policy.rules:
                rows.append(
                    [
                        policy.id,
                        rule.id,
                        rule.action,
                        rule.condition or "true",
                        rule.decision,
                        rule.required_role or "-",
                    ]
                )
        table("Regras", ["política", "regra", "ação", "condição", "decisão", "papel"], rows)


@app.command(name="test")
def test_policy(
    tool: str = typer.Argument(..., help="Ação/ferramenta a avaliar (ex: python.execute)"),
    arg: list[str] = typer.Option([], "--arg", "-a", help="Contexto: chave=valor"),
    env: str = typer.Option(None, "--env", help="development | staging | production"),
    agent: str = typer.Option(None, "--agent"),
    trace: bool = typer.Option(False, "--trace", help="Mostrar o trace completo"),
    workspace: Path = typer.Option(None, "--workspace", "-w"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Avalia uma ação contra o Policy Engine (sem executar)."""

    from ..formatting import parse_kv

    runtime = get_runtime(workspace)
    request, decision = runtime.request_action(
        tool,
        parse_kv(arg),
        agent_id=agent,
        environment=env,
        rationale="egr policy test",
    )
    if as_json:
        json_output(decision.model_dump(mode="json"))
        return

    kv(
        f"Decisão — {tool}",
        {
            "decisão": decision.decision,
            "regra": decision.rule_id or "-",
            "política": decision.policy_id or "-",
            "motivo": decision.reason,
            "papel exigido": decision.required_role or "-",
            "ambiente": request.environment,
        },
    )
    if trace:
        agent_spec = runtime.resolve_agent(agent) if agent else None
        from ...policies.engine import PolicyContext

        risk = runtime.tools.get(request.tool).spec.risk if runtime.tools.has(request.tool) else "low"
        context = PolicyContext(
            environment=request.environment,
            agent=agent_spec,
            enterprise_settings=runtime.settings.enterprise.settings.model_dump(),
            risk=risk,
            security=runtime.security_context,
        )
        explanation = runtime.policy.explain(request, context)
        table(
            "Trace",
            ["política", "regra", "ação", "condição", "match", "condição ok", "decisão"],
            [
                [
                    item["policy"],
                    item["rule"],
                    item["action"],
                    item["condition"],
                    "sim" if item["matched_action"] else "-",
                    "sim" if item["condition_ok"] else (item["condition_error"] or "não"),
                    item["decision"],
                ]
                for item in explanation["trace"]
            ],
        )


@app.command(name="sync")
def sync(
    workspace: Path = typer.Option(None, "--workspace", "-w"),
):
    """Carrega policies/*.yaml para dentro do Runtime."""

    runtime = get_runtime(workspace)
    policies = runtime.sync_policies()
    success(f"{len(policies)} política(s) carregada(s): {', '.join(policy.id for policy in policies) or '-'}")


__all__ = ["app"]
