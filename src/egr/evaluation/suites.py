"""Suítes derivadas: o primeiro teste de um artefato é o que o próprio Runtime
consegue inferir da declaração dele.

Uma ferramenta declara parâmetros — logo o Runtime sabe cobrar (1) que ela
recuse chamada sem argumentos obrigatórios e (2) que ela responda a uma chamada
válida. Uma política declara regras — logo dá para cobrar que cada regra produza
a decisão que promete. Isso não substitui escrever casos de negócio: garante que
nenhum artefato novo entre no workspace sem **nenhuma** prova.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import ConfigError
from ..domain.evaluation import EvaluationCase, EvaluationSuite, Thresholds

SAMPLES = {
    "string": "teste",
    "text": "teste",
    "number": 1.0,
    "integer": 1,
    "boolean": True,
    "object": {},
    "array": [],
    "path": "egr.yaml",
}


def sample_args(parameters: dict) -> dict:
    """Argumentos plausíveis a partir do JSON-schema-ish declarado no ToolSpec."""

    args: dict[str, Any] = {}
    for name, schema in (parameters or {}).items():
        if not isinstance(schema, dict):
            args[name] = "teste"
            continue
        kind = str(schema.get("type", "string")).lower()
        if "default" in schema:
            args[name] = schema["default"]
        elif kind in SAMPLES:
            args[name] = SAMPLES[kind]
        elif schema.get("enum"):
            args[name] = schema["enum"][0]
        else:
            args[name] = "teste"
    return args


def required_parameters(parameters: dict) -> list[str]:
    return [
        name
        for name, schema in (parameters or {}).items()
        if isinstance(schema, dict) and schema.get("required")
    ]


def smoke_suite_for_tool(runtime: Any, tool_name: str) -> EvaluationSuite:
    if not runtime.tools.has(tool_name):
        raise ConfigError(f"ferramenta não registrada: {tool_name}")
    spec = runtime.tools.get(tool_name).spec
    cases: list[EvaluationCase] = []

    if required_parameters(spec.parameters):
        cases.append(
            EvaluationCase(
                id="sem-argumentos",
                name="recusa chamada sem argumentos obrigatórios",
                description="ferramenta deve recusar (não quebrar) quando falta parâmetro obrigatório",
                args={},
                expect_ok=False,
            )
        )

    cases.append(
        EvaluationCase(
            id="chamada-valida",
            name="responde a uma chamada válida",
            args=sample_args(spec.parameters),
            expect_ok=True,
            max_duration_ms=15_000,
        )
    )
    cases.append(
        EvaluationCase(
            id="nao-vaza-erro-interno",
            name="não estoura exceção não tratada",
            args=sample_args(spec.parameters),
            expect=["not contains(str(error or ''), 'Traceback')"],
        )
    )
    return EvaluationSuite(
        id=f"smoke-{tool_name}",
        name=f"Smoke: {tool_name}",
        description="suíte mínima derivada da declaração da ferramenta",
        target_kind="tool",
        target=tool_name,
        cases=cases,
        thresholds=Thresholds(min_pass_rate=1.0, max_regressions=0),
        tags=["smoke", "gerada"],
        metadata={"generated_by": "runtime"},
    )


def smoke_suite_for_workflow(runtime: Any, workflow_id: str) -> EvaluationSuite:
    if workflow_id not in runtime.workflows:
        raise ConfigError(f"workflow não encontrado: {workflow_id}")
    workflow = runtime.workflows[workflow_id]
    return EvaluationSuite(
        id=f"smoke-{workflow_id}",
        name=f"Smoke: {workflow_id}",
        description="execução mínima do workflow — o grafo precisa terminar sem falha",
        target_kind="workflow",
        target=workflow_id,
        cases=[
            EvaluationCase(
                id="execucao-minima",
                name="roda até o fim",
                args=dict(workflow.inputs or {}),
                expect=["status != 'failed'"],
                max_duration_ms=120_000,
            )
        ],
        thresholds=Thresholds(min_pass_rate=1.0),
        tags=["smoke", "gerada"],
        metadata={"generated_by": "runtime"},
    )


def smoke_suite_for_policy(runtime: Any, policy_id: str) -> EvaluationSuite:
    """Gera um caso por regra **incondicional e sem sombra**.

    Regra com `condition` depende de contexto (amount, environment, risk,
    external_ai) que o Runtime não inventa. Ação com mais de uma regra depende
    da ordem e das condições das anteriores. Nos dois casos o honesto é
    declarar a lacuna e deixar o caso para quem conhece o domínio.
    """

    policy = next((item for item in runtime.policy.list_policies() if item.id == policy_id), None)
    if policy is None:
        raise ConfigError(f"política não encontrada: {policy_id}")

    actions: dict[str, int] = {}
    for rule in policy.rules:
        actions[rule.action] = actions.get(rule.action, 0) + 1

    cases: list[EvaluationCase] = []
    skipped: list[str] = []
    for rule in policy.rules:
        if rule.action in ("", "*") or rule.action.endswith(".*"):
            skipped.append(f"{rule.id} (curinga)")
            continue
        if rule.condition:
            skipped.append(f"{rule.id} (condicional)")
            continue
        if actions.get(rule.action, 0) > 1:
            skipped.append(f"{rule.id} (ação com {actions[rule.action]} regras)")
            continue
        environment = rule.environments[0] if rule.environments else "development"
        cases.append(
            EvaluationCase(
                id=f"regra-{rule.id}",
                name=f"regra '{rule.id}' decide {rule.decision}",
                args={"tool": rule.action, "environment": str(environment)},
                expect=[f"decision == '{rule.decision}'"],
            )
        )
    if not cases:
        raise ConfigError(
            f"política '{policy_id}' não tem regra simulável automaticamente "
            f"(fora do escopo: {', '.join(skipped) or '-'})"
        )
    return EvaluationSuite(
        id=f"smoke-{policy_id}",
        name=f"Smoke: {policy_id}",
        description="cada regra incondicional precisa produzir a decisão que declara",
        target_kind="policy",
        target=policy_id,
        cases=cases,
        thresholds=Thresholds(min_pass_rate=1.0),
        tags=["smoke", "gerada"],
        metadata={"generated_by": "runtime", "fora_do_escopo": skipped},
    )


def smoke_suite_for_agent(runtime: Any, agent_id: str) -> EvaluationSuite:
    if agent_id not in runtime.agents:
        raise ConfigError(f"agente não encontrado: {agent_id}")
    agent = runtime.agents[agent_id]
    return EvaluationSuite(
        id=f"smoke-{agent_id}",
        name=f"Smoke: {agent_id}",
        description=(
            "task mínima com o agente: prova o encanamento (política, ferramentas, memória). "
            "Com provedor local, não mede qualidade de modelo."
        ),
        target_kind="agent",
        target=agent_id,
        cases=[
            EvaluationCase(
                id="task-minima",
                name="executa uma task até o fim",
                args={"objective": agent.objective or "Liste o que você pode fazer neste workspace."},
                expect=["status == 'completed'"],
                max_duration_ms=120_000,
            )
        ],
        thresholds=Thresholds(min_pass_rate=1.0),
        tags=["smoke", "gerada"],
        metadata={"generated_by": "runtime", "capability": agent.model.capability},
    )


def smoke_suite(runtime: Any, target_kind: str, target: str) -> EvaluationSuite:
    if target_kind == "tool":
        return smoke_suite_for_tool(runtime, target)
    if target_kind == "workflow":
        return smoke_suite_for_workflow(runtime, target)
    if target_kind == "policy":
        return smoke_suite_for_policy(runtime, target)
    if target_kind == "agent":
        return smoke_suite_for_agent(runtime, target)
    raise ConfigError(f"não sei gerar suíte para '{target_kind}'")


__all__ = [
    "required_parameters",
    "sample_args",
    "smoke_suite",
    "smoke_suite_for_agent",
    "smoke_suite_for_policy",
    "smoke_suite_for_tool",
    "smoke_suite_for_workflow",
]
