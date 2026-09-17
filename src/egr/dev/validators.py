"""Verificação estática de propostas — o Runtime lê o código antes de aceitá-lo.

Nada aqui executa o conteúdo proposto. As verificações são de três tipos:

1. **declaração** — o YAML/Python parseia e casa com o modelo de domínio;
2. **governo** — o artefato não escala privilégio, não nasce em produção e tem
   autor conhecido;
3. **revisão** — o código é legível e não usa construções que escapam do
   sandbox (eval/exec/subprocess/rede/serialização).

Ferramentas (código) recebem ainda a análise de AST. O objetivo não é provar
que o código é bom — é impedir as classes de abuso que conhecemos e tornar o
resto revisável por um humano.
"""

from __future__ import annotations

import ast
from typing import Any

import yaml

from ..domain.agent import AgentSpec
from ..domain.enums import Environment, ProposalKind, RiskLevel
from ..domain.policy import Policy
from ..domain.proposal import ChangeProposal, ValidationCheck
from ..domain.workflow import Workflow

# ---- limites e listas -------------------------------------------------
MAX_TOOL_LINES = 400
MAX_TOOL_CHARS = 20_000

ALLOWED_IMPORTS = {
    "__future__",
    "abc",
    "base64",
    "collections",
    "contextlib",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fnmatch",
    "fractions",
    "hashlib",
    "html",
    "io",
    "itertools",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "statistics",
    "string",
    "textwrap",
    "time",
    "typing",
    "unicodedata",
    "urllib.parse",
    "uuid",
    "egr",
}

FORBIDDEN_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "input",
    "globals",
    "locals",
    "vars",
    "setattr",
    "delattr",
    "memoryview",
}

FORBIDDEN_MODULE_CALLS = {
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.fork",
    "os.kill",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.chmod",
    "os.chown",
    "os.putenv",
    "os.environ",
    "shutil.rmtree",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_output",
    "socket.socket",
    "socket.create_connection",
    "importlib.import_module",
    "pickle.loads",
    "pickle.load",
    "ctypes.CDLL",
    "multiprocessing.Process",
    "signal.alarm",
    "urllib.request.urlopen",
    "http.client.HTTPSConnection",
    "http.client.HTTPConnection",
    "smtplib.SMTP",
    "ftplib.FTP",
    "webbrowser.open",
    "platform.system",
}

FORBIDDEN_ATTRIBUTES = {
    "__class__",
    "__bases__",
    "__subclasses__",
    "__globals__",
    "__builtins__",
    "__code__",
    "__mro__",
    "__dict__",
    "__reduce__",
    "__init_subclass__",
}

RISK_NAMES = {level.value for level in RiskLevel}

# valor de argumento que não pudemos resolver sem executar o código
DYNAMIC = "<expressão>"


def _check(name: str, ok: bool, level: str = "error", detail: str = "") -> ValidationCheck:
    return ValidationCheck(name=name, ok=ok, level=level if not ok else "info", detail=detail)


# ---- ponto de entrada -------------------------------------------------


def validate_proposal(proposal: ChangeProposal, runtime: Any) -> list[ValidationCheck]:
    """Valida uma proposta contra o workspace atual. Nunca executa o conteúdo."""

    checks: list[ValidationCheck] = [_check("conteúdo", bool(proposal.content.strip()), detail="conteúdo vazio")]

    origin_error = _check_origin(proposal, runtime)
    checks.append(origin_error)

    environment_problem = _check_environment(proposal)
    if environment_problem:
        checks.append(environment_problem)

    if proposal.kind == ProposalKind.TOOL:
        checks.extend(_validate_tool(proposal, runtime))
    elif proposal.kind == ProposalKind.AGENT:
        checks.extend(_validate_agent(proposal, runtime))
    elif proposal.kind == ProposalKind.WORKFLOW:
        checks.extend(_validate_workflow(proposal, runtime))
    elif proposal.kind == ProposalKind.POLICY:
        checks.extend(_validate_policy(proposal, runtime))
    else:
        checks.append(_check("tipo", False, detail=f"tipo de proposta desconhecido: {proposal.kind}"))

    collision = _check_collision(proposal, runtime)
    if collision:
        checks.append(collision)

    if not proposal.content_matches_fingerprint and proposal.fingerprint:
        checks.append(
            _check(
                "integridade",
                False,
                detail="o conteúdo mudou depois de registrado: a proposta precisa ser recriada",
            )
        )
    return checks


# ---- origem, ambiente e colisão --------------------------------------


def _check_origin(proposal: ChangeProposal, runtime: Any) -> ValidationCheck:
    """Toda proposta precisa de um autor conhecido: agente existente ou humano."""

    origin = proposal.origin or ""
    if origin.startswith("agent:"):
        agent_id = origin.split(":", 1)[1]
        if agent_id and runtime.agents.get(agent_id):
            return _check("autoria", True, detail=f"agente {agent_id}")
        return _check("autoria", False, detail=f"agente de origem desconhecido: {agent_id or '(vazio)'}")
    if origin.startswith("human:"):
        actor = origin.split(":", 1)[1]
        if actor.strip():
            return _check("autoria", True, detail=f"humano {actor}")
        return _check("autoria", False, detail="origem 'human:' sem ator")
    return _check("autoria", False, detail=f"origem inválida: {origin!r} (use agent:<id> ou human:<ator>)")


def _check_environment(proposal: ChangeProposal) -> ValidationCheck | None:
    """Propostas nascem em development: promoção para produção é Fase 9."""

    if str(proposal.environment) == Environment.PRODUCTION:
        return _check(
            "ambiente",
            False,
            detail="propostas não nascem em produção — promoção dev→staging→produção é Fase 9",
        )
    return None


def _check_collision(proposal: ChangeProposal, runtime: Any) -> ValidationCheck | None:
    exists = False
    if proposal.kind == ProposalKind.AGENT:
        exists = proposal.name in runtime.agents
    elif proposal.kind == ProposalKind.WORKFLOW:
        exists = proposal.name in runtime.workflows
    elif proposal.kind == ProposalKind.POLICY:
        exists = any(policy.id == proposal.name for policy in _policies(runtime))
    elif proposal.kind == ProposalKind.TOOL:
        exists = runtime.tools.has(proposal.name)
    if not exists:
        return None
    return _check(
        "colisão",
        False,
        level="warning",
        detail=f"já existe {proposal.kind} '{proposal.name}': aplicar substitui o artefato atual",
    )


def _policies(runtime: Any) -> list[Policy]:
    try:
        return list(runtime.policy.list_policies())
    except Exception:  # pragma: no cover - defensivo
        return []


# ---- YAML -------------------------------------------------------------


def _parse_yaml(proposal: ChangeProposal) -> tuple[Any | None, ValidationCheck | None]:
    try:
        return yaml.safe_load(proposal.content), None
    except yaml.YAMLError as exc:
        return None, _check("sintaxe", False, detail=f"YAML inválido: {exc}")


def _single(raw: Any, key: str) -> Any:
    if isinstance(raw, dict):
        if key in raw and isinstance(raw[key], list):
            items = raw[key]
            return items[0] if len(items) == 1 else None
        return raw
    return raw


# ---- agentes ----------------------------------------------------------


def _validate_agent(proposal: ChangeProposal, runtime: Any) -> list[ValidationCheck]:
    raw, error = _parse_yaml(proposal)
    if error:
        return [error]

    item = _single(raw, "agents")
    if not isinstance(item, dict):
        return [_check("declaração", False, detail="esperado um agente (mapa) ou {agents: [ ... ]}")]

    checks = [_check_declared_name(item.get("id"), proposal)]

    try:
        agent = AgentSpec.model_validate(item)
    except Exception as exc:
        checks.append(_check("declaração", False, detail=str(exc)[:300]))
        return checks
    checks.append(_check("declaração", True, detail=f"{agent.id}@{agent.version}"))

    if not agent.objective.strip():
        checks.append(_check("objetivo", False, detail="agente sem 'objective'"))

    capability = agent.model.capability
    known = _known_capabilities(runtime)
    if capability not in known:
        known_list = ", ".join(sorted(known))
        checks.append(
            _check("modelo", False, detail=f"capacidade desconhecida: {capability} (conhecidas: {known_list})")
        )
    if agent.model.temperature < 0 or agent.model.temperature > 2:
        checks.append(_check("modelo", False, detail="temperature fora de 0..2"))

    checks.extend(_check_escalation(agent, proposal, runtime))
    return checks


def _known_capabilities(runtime: Any) -> set[str]:
    capabilities = {"reasoning", "fast", "vision", "embedding", "code"}
    try:
        for provider in runtime.gateway.list_providers():
            for capability in provider.get("capabilities", []) or []:
                capabilities.add(capability)
    except Exception:  # pragma: no cover - defensivo
        pass
    return capabilities


def _check_escalation(agent: AgentSpec, proposal: ChangeProposal, runtime: Any) -> list[ValidationCheck]:
    """Um agente não pode conceder a outro mais do que ele mesmo tem."""

    agent_id = proposal.created_by_agent
    checks: list[ValidationCheck] = []
    if not agent_id:
        return checks
    origin = runtime.agents.get(agent_id)
    if origin is None:
        return checks

    if not agent.permissions.tools:
        checks.append(
            _check(
                "escalada",
                False,
                level="warning",
                detail="sem 'permissions.tools': o agente herda a política em vez de declarar o mínimo necessário",
            )
        )
    else:
        offenders: list[str] = []
        for tool_name in _tool_names(runtime):
            if agent.allows_tool(tool_name) and not origin.allows_tool(tool_name):
                offenders.append(tool_name)
        if offenders:
            shown = ", ".join(offenders[:5]) + ("…" if len(offenders) > 5 else "")
            checks.append(
                _check(
                    "escalada",
                    False,
                    detail=(f"o agente '{agent_id}' não tem as ferramentas que esta proposta concede: {shown}"),
                )
            )

    if agent.risk_rank > origin.risk_rank:
        checks.append(
            _check(
                "escalada",
                False,
                detail=(
                    f"max_risk '{agent.permissions.max_risk}' acima do agente de origem"
                    f" ('{origin.permissions.max_risk}')"
                ),
            )
        )
    if origin.permissions.namespaces and agent.permissions.namespaces:
        forbidden = [ns for ns in agent.permissions.namespaces if ns not in origin.permissions.namespaces]
        if forbidden:
            checks.append(
                _check(
                    "escalada",
                    False,
                    detail=f"namespaces fora do alcance do agente de origem: {', '.join(forbidden)}",
                )
            )
    return checks


def _tool_names(runtime: Any) -> list[str]:
    return [tool["name"] for tool in runtime.tools.list()]


# ---- workflows --------------------------------------------------------


def _validate_workflow(proposal: ChangeProposal, runtime: Any) -> list[ValidationCheck]:
    raw, error = _parse_yaml(proposal)
    if error:
        return [error]

    item = _single(raw, "workflows")
    if not isinstance(item, dict):
        return [_check("declaração", False, detail="esperado um workflow (mapa) ou {workflows: [ ... ]}")]

    checks = [_check_declared_name(item.get("id"), proposal)]
    try:
        workflow = Workflow.model_validate(item)
    except Exception as exc:
        checks.append(_check("declaração", False, detail=str(exc)[:300]))
        return checks

    from ..runtime.workflow_engine import WorkflowEngine

    try:
        problems = WorkflowEngine(runtime).validate(workflow)
    except Exception as exc:  # pragma: no cover - defensivo
        problems = [str(exc)]
    if problems:
        for problem in problems[:5]:
            checks.append(_check("grafo", False, detail=problem))
    else:
        checks.append(_check("grafo", True, detail=f"{len(workflow.steps)} passo(s)"))

    known_agents = set(runtime.agents)
    unknown = sorted({step.agent for step in workflow.steps if step.agent and step.agent not in known_agents})
    if unknown:
        checks.append(_check("agentes", False, detail=f"agentes inexistentes referenciados: {', '.join(unknown)}"))
    return checks


# ---- políticas --------------------------------------------------------


def _validate_policy(proposal: ChangeProposal, runtime: Any) -> list[ValidationCheck]:
    raw, error = _parse_yaml(proposal)
    if error:
        return [error]

    item = _single(raw, "policies")
    if not isinstance(item, dict):
        return [_check("declaração", False, detail="esperado uma policy (mapa) ou {policies: [ ... ]}")]

    checks = [_check_declared_name(item.get("id"), proposal)]
    try:
        policy = Policy.model_validate(item)
    except Exception as exc:
        checks.append(_check("declaração", False, detail=str(exc)[:300]))
        return checks

    if not policy.rules:
        checks.append(_check("regras", False, detail="política sem regras"))

    from ..policies.conditions import Condition, ConditionError

    for rule in policy.rules:
        if rule.condition:
            try:
                Condition(rule.condition)
            except ConditionError as exc:
                checks.append(_check("regras", False, detail=f"regra '{rule.id}' com condição inválida: {exc}"))
        if str(rule.decision) == "allow" and rule.action in ("*", ""):
            checks.append(
                _check(
                    "regras",
                    False,
                    level="warning",
                    detail=f"regra '{rule.id}' permite todas as ações ('*') — default deny enfraquecido",
                )
            )
    if not checks or all(check.ok for check in checks):
        checks.append(_check("regras", True, detail=f"{len(policy.rules)} regra(s)"))
    return checks


def _check_declared_name(declared: Any, proposal: ChangeProposal) -> ValidationCheck:
    if declared is None:
        return _check("nome", False, detail="o conteúdo não declara 'id'/'name'")
    if str(declared) != proposal.name:
        return _check("nome", False, detail=f"declarado '{declared}', proposto '{proposal.name}'")
    return _check("nome", True, detail=str(declared))


# ---- ferramentas (código) ---------------------------------------------


def validate_tool_source(
    source: str,
    expected_name: str | None = None,
    *,
    namespace: str | None = None,
) -> list[ValidationCheck]:
    """Análise estática de uma ferramenta escrita por agente (ou humano).

    `expected_name` exige que o módulo declare exatamente aquela ferramenta;
    `namespace` (ex.: "exemplo.") apenas confina os nomes declarados ao prefixo.
    """

    checks: list[ValidationCheck] = []
    if len(source) > MAX_TOOL_CHARS:
        checks.append(
            _check(
                "tamanho",
                False,
                detail=f"{len(source)} caracteres (limite {MAX_TOOL_CHARS}) — divida em partes revisáveis",
            )
        )
    lines = source.splitlines()
    if len(lines) > MAX_TOOL_LINES:
        checks.append(_check("tamanho", False, detail=f"{len(lines)} linhas (limite {MAX_TOOL_LINES})"))

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [*_check_syntax(source, exc)]

    checks.extend(_check_structure(tree, expected_name, namespace))
    checks.extend(_check_imports(tree))
    checks.extend(_check_dangerous(tree))
    return checks


def _check_syntax(source: str, exc: SyntaxError) -> list[ValidationCheck]:
    line = exc.lineno or 0
    snippet = ""
    if 0 < line <= len(source.splitlines()):
        snippet = source.splitlines()[line - 1].strip()[:80]
    return [
        _check(
            "sintaxe",
            False,
            detail=f"linha {line}: {exc.msg}{(' → ' + snippet) if snippet else ''}",
        )
    ]


def _check_structure(
    tree: ast.AST, expected_name: str | None = None, namespace: str | None = None
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []

    allowed_top = (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef, ast.Assign, ast.AnnAssign)
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, allowed_top):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring de módulo
        checks.append(
            _check(
                "estrutura",
                False,
                detail=(
                    f"código solto no nível superior (linha {node.lineno}): "
                    "só imports, definições e atribuições — nada executa na importação"
                ),
            )
        )

    classes = [
        node
        for node in tree.body  # type: ignore[attr-defined]
        if isinstance(node, ast.ClassDef) and any(_base_name(base) == "Tool" for base in node.bases)
    ]
    if not classes:
        checks.append(_check("estrutura", False, detail="nenhuma classe que herde de Tool"))
        return checks

    declared: list[str] = []
    for cls in classes:
        names = {node.name for node in cls.body if isinstance(node, ast.FunctionDef)}
        if "execute" not in names:
            checks.append(
                _check("contrato", False, detail=f"{cls.name} não implementa execute(self, request, ctx)")
            )
        else:
            execute = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "execute")
            if len(execute.args.args) < 3:
                checks.append(
                    _check(
                        "contrato",
                        False,
                        detail=f"{cls.name}.execute() precisa da assinatura (self, request, ctx)",
                    )
                )
        spec = _toolspec_call(cls)
        if spec is None:
            checks.append(_check("contrato", False, detail=f"{cls.name} não declara 'spec = ToolSpec(...)'"))
            continue
        checks.extend(_check_spec(spec, cls.name, declared))

    if expected_name and expected_name not in declared:
        checks.append(
            _check(
                "nome",
                False,
                detail=(
                    f"nenhuma ferramenta declarada como '{expected_name}'"
                    f" (encontradas: {', '.join(declared) or '-'})"
                ),
            )
        )
    prefix = namespace or (f"{expected_name.split('.')[0]}." if expected_name else None)
    foreign = [name for name in declared if prefix and not name.startswith(prefix)]
    if foreign:
        checks.append(
            _check(
                "namespace",
                False,
                detail=(
                    f"ferramentas fora do namespace '{prefix}': {', '.join(foreign)}"
                    " — uma proposta não registra ferramenta alheia"
                ),
            )
        )
    return checks


def _check_spec(spec: dict, class_name: str, declared: list[str]) -> list[ValidationCheck]:
    """Confere o ToolSpec declarado sem executar o módulo."""

    declared.append(spec.get("name") if isinstance(spec.get("name"), str) else "")
    checks: list[ValidationCheck] = []
    name = spec.get("name", DYNAMIC)
    if name == DYNAMIC:
        checks.append(_check("nome", False, detail=f"{class_name}: ToolSpec.name precisa ser uma string literal"))
    risk = spec.get("risk")
    if isinstance(risk, str) and risk != DYNAMIC and risk not in RISK_NAMES:
        risks = ", ".join(sorted(RISK_NAMES))
        checks.append(_check("contrato", False, detail=f"risk inválido: {risk} (use {risks})"))
    if "description" not in spec:
        checks.append(
            _check(
                "contrato",
                False,
                level="warning",
                detail=f"{class_name}: ToolSpec sem 'description' — o planejador não saberá quando usá-la",
            )
        )
    return checks


def _check_imports(tree: ast.AST) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    checks.append(_check("imports", False, detail=f"import proibido: {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                checks.append(_check("imports", False, detail="import relativo proibido"))
                continue
            module = node.module or ""
            root = module.split(".")[0]
            if root not in ALLOWED_IMPORTS:
                checks.append(_check("imports", False, detail=f"import proibido: from {module} import ..."))
    return checks


def _check_dangerous(tree: ast.AST) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _dotted(node.func)
            if (target and target in FORBIDDEN_MODULE_CALLS) or (target and target.split(".")[-1] in FORBIDDEN_CALLS):
                checks.append(_check("segurança", False, detail=f"chamada proibida: {target}()"))
            if target == "getattr":
                dynamic = len(node.args) > 1 and not isinstance(node.args[1], ast.Constant)
                if dynamic:
                    checks.append(_check("segurança", False, detail="getattr() com atributo dinâmico"))
            if target == "open" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and _suspicious_path(first.value):
                    checks.append(
                        _check(
                            "segurança",
                            False,
                            detail=f"open() com caminho suspeito: {first.value!r} — use ctx.workspace/ctx.sandbox",
                        )
                    )
            if target == "time.sleep":
                checks.append(
                    _check(
                        "segurança",
                        False,
                        level="warning",
                        detail="time.sleep(): prefira deixar o timeout com o Runtime",
                    )
                )
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            checks.append(_check("segurança", False, detail=f"acesso proibido a atributo interno: .{node.attr}"))
        elif isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
            if not any(isinstance(inner, ast.Break) for inner in ast.walk(node)):
                checks.append(
                    _check("revisão", False, level="warning", detail="laço infinito (while True sem break)")
                )
    return checks


def _suspicious_path(value: str) -> bool:
    parts = value.replace("\\", "/").split("/")
    return value.startswith(("/", "~")) or ".." in parts


def _validate_tool(proposal: ChangeProposal, runtime: Any) -> list[ValidationCheck]:
    del runtime  # a validação de ferramenta é puramente estática
    return validate_tool_source(proposal.content, proposal.name)


# ---- helpers de AST ---------------------------------------------------


def _base_name(node: ast.expr) -> str:
    return _dotted(node) or ""


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return None


def _toolspec_call(cls: ast.ClassDef) -> dict | None:
    """Extrai os argumentos nomeados de `spec = ToolSpec(...)` sem executar nada."""

    for node in ast.walk(cls):
        if not isinstance(node, ast.Assign):
            continue
        targets = {_dotted(target) for target in node.targets if _dotted(target)}
        if "spec" not in targets:
            continue
        value = node.value
        if isinstance(value, ast.Call) and _dotted(value.func) in ("ToolSpec", "tool.ToolSpec"):
            extracted: dict[str, Any] = {}
            for keyword in value.keywords:
                if keyword.arg is None:
                    continue
                if isinstance(keyword.value, ast.Constant):
                    extracted[keyword.arg] = keyword.value.value
                elif isinstance(keyword.value, ast.Dict):
                    extracted[keyword.arg] = {k.value for k in keyword.value.keys if isinstance(k, ast.Constant)}
                elif isinstance(keyword.value, ast.List):
                    extracted[keyword.arg] = [
                        item.value for item in keyword.value.elts if isinstance(item, ast.Constant)
                    ]
                else:
                    extracted[keyword.arg] = DYNAMIC
            return extracted
    return None


__all__ = ["MAX_TOOL_CHARS", "MAX_TOOL_LINES", "validate_proposal", "validate_tool_source"]
