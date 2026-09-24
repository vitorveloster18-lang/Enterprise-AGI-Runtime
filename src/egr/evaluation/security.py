"""Varredura de segurança do alvo avaliado (Fase 8).

Não é um scanner de vulnerabilidades: é a conferência, do ponto de vista do
Runtime, de que o artefato não foi escrito de forma a enfraquecer o governo —
permissões largas demais, objetivo vazio, ferramenta sem política, regra que
libera tudo.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import FindingSeverity
from ..domain.evaluation import SecurityFinding


def scan(runtime: Any, target_kind: str, target: str) -> list[SecurityFinding]:
    """Devolve achados para o alvo. Lista vazia = nada a declarar."""

    if target_kind == "tool":
        return _scan_tool(runtime, target)
    if target_kind == "workflow":
        return _scan_workflow(runtime, target)
    if target_kind == "agent":
        return _scan_agent(runtime, target)
    if target_kind == "policy":
        return _scan_policy(runtime, target)
    return [
        SecurityFinding(
            code="alvo-desconhecido",
            severity=FindingSeverity.WARNING,
            detail=f"não sei avaliar '{target_kind}'",
        )
    ]


def _finding(code: str, detail: str, severity: FindingSeverity = FindingSeverity.WARNING) -> SecurityFinding:
    return SecurityFinding(code=code, detail=detail, severity=severity)


# ---- ferramentas ------------------------------------------------------
def _scan_tool(runtime: Any, name: str) -> list[SecurityFinding]:
    from pathlib import Path

    from ..dev.validators import validate_tool_source

    findings: list[SecurityFinding] = []
    tools_dir = Path(runtime.settings.workspace) / "tools"
    source: str | None = None
    for path in sorted(tools_dir.glob("*.py")) if tools_dir.exists() else []:
        content = path.read_text(encoding="utf-8")
        if f'name="{name}"' in content or f"name='{name}'" in content:
            source = content
            break

    if not runtime.tools.has(name):
        findings.append(_finding("ferramenta-ausente", f"'{name}' não está registrada", FindingSeverity.CRITICAL))
        return findings

    if source:
        for check in validate_tool_source(source, namespace=f"{name.split('.')[0]}."):
            if check.ok:
                continue
            severity = FindingSeverity.CRITICAL if check.level == "error" else FindingSeverity.WARNING
            findings.append(_finding(f"codigo:{check.name}", check.detail, severity))

    spec = runtime.tools.get(name).spec
    if str(spec.risk) in ("high", "critical") and not spec.side_effects:
        findings.append(
            _finding(
                "risco-declarado",
                f"risk={spec.risk} sem side_effects=True: o risco declarado não casa com o efeito",
            )
        )
    if spec.side_effects and not _has_policy_for(runtime, name):
        findings.append(
            _finding("sem-politica", f"tem efeito colateral mas nenhuma regra de política menciona '{name}'")
        )
    if not spec.parameters:
        findings.append(
            _finding("sem-parametros", "ToolSpec sem parâmetros declarados: nada a validar na chamada")
        )
    return findings


def _has_policy_for(runtime: Any, tool: str) -> bool:
    for policy in runtime.policy.list_policies():
        for rule in policy.rules:
            if rule.action in (tool, "*") or (
                rule.action.endswith(".*") and tool.startswith(rule.action[:-1])
            ):
                return True
    return False


# ---- workflows --------------------------------------------------------
def _scan_workflow(runtime: Any, workflow_id: str) -> list[SecurityFinding]:
    from ..runtime.workflow_engine import WorkflowEngine

    findings: list[SecurityFinding] = []
    workflow = runtime.workflows.get(workflow_id)
    if workflow is None:
        return [_finding("workflow-ausente", f"'{workflow_id}' não existe", FindingSeverity.CRITICAL)]

    for problem in WorkflowEngine(runtime).validate(workflow):
        findings.append(_finding("grafo", problem, FindingSeverity.CRITICAL))

    for step in workflow.steps:
        agent = runtime.agents.get(step.agent) if step.agent else None
        if step.agent and agent is None:
            findings.append(
                _finding("agente-inexistente", f"passo '{step.id}' usa o agente '{step.agent}', que não existe",
                         FindingSeverity.CRITICAL)
            )
            continue
        if step.tool and agent is not None and not agent.allows_tool(step.tool):
            findings.append(
                _finding(
                    "ferramenta-nao-permitida",
                    f"passo '{step.id}' chama '{step.tool}', fora do alcance do agente '{step.agent}'",
                    FindingSeverity.CRITICAL,
                )
            )
        if step.tool and not runtime.tools.has(step.tool):
            findings.append(
                _finding("ferramenta-inexistente", f"passo '{step.id}' chama '{step.tool}', que não está registrada",
                         FindingSeverity.CRITICAL)
            )
        if not step.objective and not step.tool:
            findings.append(
                _finding("passo-vazio", f"passo '{step.id}' sem objetivo e sem ferramenta", FindingSeverity.CRITICAL)
            )
        if step.environment == "production" and not step.policy:
            findings.append(
                _finding("producao-sem-politica", f"passo '{step.id}' roda em produção sem política declarada")
            )
    if not workflow.steps:
        findings.append(_finding("sem-passos", "workflow sem passos", FindingSeverity.CRITICAL))
    return findings


# ---- agentes ----------------------------------------------------------
def _scan_agent(runtime: Any, agent_id: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    agent = runtime.agents.get(agent_id)
    if agent is None:
        return [_finding("agente-ausente", f"'{agent_id}' não existe", FindingSeverity.CRITICAL)]

    if not agent.objective.strip():
        findings.append(_finding("sem-objetivo", "agente sem 'objective'", FindingSeverity.CRITICAL))
    if not agent.permissions.tools:
        findings.append(
            _finding("permissoes-vazias", "sem 'permissions.tools': herda a política em vez de declarar o mínimo")
        )
    if "*" in agent.permissions.tools:
        findings.append(
            _finding("curinga", "permissions.tools contém '*': o agente pode usar qualquer ferramenta",
                     FindingSeverity.CRITICAL)
        )
    for tool in agent.permissions.tools:
        if tool == "*":
            continue
        if tool.endswith(".*"):
            continue
        if not runtime.tools.has(tool):
            findings.append(_finding("ferramenta-inexistente", f"declara '{tool}', que não está registrada"))
    if str(agent.permissions.max_risk) in ("high", "critical") and not agent.permissions.tools:
        findings.append(
            _finding("risco-sem-lista", f"max_risk={agent.permissions.max_risk} sem lista de ferramentas")
        )
    if not agent.memory:
        findings.append(_finding("sem-memoria", "agente sem namespaces de memória declarados"))
    capability = agent.model.capability
    known = {"reasoning", "fast", "vision", "embedding", "code"}
    known |= {
        item
        for provider in runtime.gateway.list_providers()
        for item in (provider.get("capabilities") or [])
    }
    if capability not in known:
        findings.append(
            _finding("capacidade-desconhecida", f"capacidade '{capability}' sem provedor", FindingSeverity.CRITICAL)
        )
    else:
        try:
            runtime.gateway.route(capability, agent.model.allow_external)
        except Exception as exc:  # pragma: no cover - depende dos provedores
            findings.append(
                _finding("sem-provedor", f"nenhum provedor atende '{capability}': {exc}", FindingSeverity.CRITICAL)
            )
    if str(agent.environment) == "production":
        findings.append(
            _finding("agente-em-producao", "agente declarado em production (promoção é Fase 9)")
        )
    return findings


# ---- políticas --------------------------------------------------------
def _scan_policy(runtime: Any, policy_id: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    policy = next((item for item in runtime.policy.list_policies() if item.id == policy_id), None)
    if policy is None:
        return [_finding("politica-ausente", f"'{policy_id}' não existe", FindingSeverity.CRITICAL)]

    if not policy.rules:
        findings.append(_finding("sem-regras", "política sem regras", FindingSeverity.CRITICAL))
    for rule in policy.rules:
        if str(rule.decision) == "allow" and rule.action in ("*", ""):
            findings.append(
                _finding(
                    "libera-tudo",
                    f"regra '{rule.id}' permite todas as ações: default deny enfraquecido",
                    FindingSeverity.CRITICAL,
                )
            )
        if not rule.reason:
            findings.append(_finding("sem-justificativa", f"regra '{rule.id}' sem 'reason'"))
    return findings


__all__ = ["scan"]
