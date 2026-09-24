"""Rascunhos: o ponto de partida de um artefato novo.

O scaffold não cria o arquivo no workspace — devolve o conteúdo de uma
**proposta**. Nenhum agente, humano ou modelo escreve direto em `agents/`,
`tools/`, `workflows/` ou `policies/`; tudo entra como proposta verificada.
"""

from __future__ import annotations

from ..domain.enums import ProposalKind

AGENT_TEMPLATE = '''\
id: {name}
name: {title}
version: 0.1.0
objective: ""
model:
  capability: reasoning
  temperature: 0.2
  max_tokens: 2048
memory:
  - default
permissions:
  tools: []
  namespaces:
    - default
  max_risk: low
environment: development
'''

WORKFLOW_TEMPLATE = '''\
id: {name}
name: {title}
version: 0.1.0
description: ""
steps:
  - id: s1
    agent: {agent}
    objective: ""
    outputs: {{}}
'''

POLICY_TEMPLATE = '''\
id: {name}
name: {title}
version: 0.1.0
description: ""
enabled: true
priority: 0
rules:
  - id: r1
    action: ""
    decision: allow
    reason: ""
'''

TOOL_TEMPLATE = '''\
"""Ferramenta {name} — criada dentro do sandbox do Runtime."""

from __future__ import annotations

from pathlib import Path

from egr.domain.enums import RiskLevel
from egr.domain.tool import ToolRequest, ToolResult, ToolSpec
from egr.tools.protocol import Tool, ToolContext


class {cls}(Tool):
    spec = ToolSpec(
        name="{name}",
        description="",
        parameters={{
            # "caminho": {{"type": "string", "required": True, "help": "relativo ao workspace"}},
        }},
        risk=RiskLevel.LOW,
        side_effects=False,
        requires_network=False,
    )

    def execute(self, request: ToolRequest, ctx: ToolContext) -> ToolResult:
        # Regras do Runtime para ferramentas:
        #   • use ctx.workspace / ctx.sandbox — nunca caminhos absolutos;
        #   • respeite ctx.dry_run: não escreva nada quando ele for True;
        #   • devolva ToolResult.success(...) ou ToolResult.failure("motivo").
        raise NotImplementedError("implemente execute()")
'''


def class_name(tool_name: str) -> str:
    parts = [part for part in tool_name.replace("-", ".").replace("_", ".").split(".") if part]
    return "".join(part.capitalize() for part in parts) + "Tool"


def scaffold(kind: str | ProposalKind, name: str, *, agent: str = "") -> str:
    """Conteúdo inicial de uma proposta, já no formato esperado pelo validador."""

    kind = str(kind)
    title = name.replace("-", " ").replace(".", " ").replace("_", " ").title()
    if kind == ProposalKind.AGENT:
        return AGENT_TEMPLATE.format(name=name, title=title)
    if kind == ProposalKind.WORKFLOW:
        return WORKFLOW_TEMPLATE.format(name=name, title=title, agent=agent or "runtime-agent")
    if kind == ProposalKind.POLICY:
        return POLICY_TEMPLATE.format(name=name, title=title)
    if kind == ProposalKind.TOOL:
        return TOOL_TEMPLATE.format(name=name, cls=class_name(name))
    raise ValueError(f"tipo desconhecido: {kind}")


__all__ = ["class_name", "scaffold"]
