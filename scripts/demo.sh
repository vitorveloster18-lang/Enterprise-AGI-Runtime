#!/usr/bin/env bash
# End-to-end demo of the Enterprise AGI Runtime (Fase 0 + Fase 1).
#
#   init -> status -> doctor -> task (dev) -> task (production, approval)
#   -> approval approve -> tools/MCP -> segurança (identidade, RBAC, cofre)
#   -> audit verify -> memory search
#
# Uso: bash scripts/demo.sh [diretório do workspace]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EGR_BIN="${EGR_BIN:-$ROOT/.venv/bin/egr}"
# por padrão o demo roda em diretório temporário para nunca sobrescrever o
# workspace de exemplo versionado; passe um caminho para usar outro destino
WORKSPACE="${1:-$(mktemp -d)/egr-demo}"

if [ ! -x "$EGR_BIN" ]; then
  echo "egr não encontrado em $EGR_BIN — rode 'make setup' primeiro."
  exit 1
fi

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

step "1/13 · init"
"$EGR_BIN" init "$WORKSPACE" --enterprise acme --name "ACME Contabilidade" --force

cd "$WORKSPACE"

step "2/13 · status"
"$EGR_BIN" status

step "3/13 · doctor"
"$EGR_BIN" doctor || true

step "4/13 · políticas e agentes declarativos"
"$EGR_BIN" agent sync
"$EGR_BIN" policy sync
"$EGR_BIN" policy test payment.create --arg amount=9000 || true

step "5/13 · primeira task autônoma local (milestone)"
"$EGR_BIN" task "Analise os documentos desta pasta e produza um relatório." --agent document-agent || true

step "6/13 · task em produção pausa para aprovação humana"
set +e
OUTPUT="$("$EGR_BIN" task "Gerar relatório consolidado do mês" --env production 2>&1)"
echo "$OUTPUT"
set -e
APPROVAL="$(echo "$OUTPUT" | grep -oE 'apr_[A-Za-z0-9_]+' | head -1 || true)"

if [ -n "$APPROVAL" ]; then
  step "7/13 · aprovação humana ($APPROVAL)"
  "$EGR_BIN" approval approve "$APPROVAL" --by "demo" --note "aprovado no demo"
else
  step "7/13 · nenhuma aprovação pendente"
fi

step "8/13 · Fase 3 — Tool Runtime (sandbox, git, MCP)"
"$EGR_BIN" tool list
"$EGR_BIN" policy test git.commit || true
"$EGR_BIN" tool test git.status --execute || true
"$EGR_BIN" mcp list || true
"$EGR_BIN" mcp call mcp.calculadora.somar --arg a=40 --arg b=2 --execute || true

step "9/13 · Fase 4 — Segurança (identidade, RBAC, cofre, chaves)"
"$EGR_BIN" key init || true
printf 'sk-demo-nao-use' | "$EGR_BIN" secret set demo-openai --provider openai --stdin || true
"$EGR_BIN" secret list || true
"$EGR_BIN" identity add vitor --name "Vitor" --roles approver || true
"$EGR_BIN" identity add ci-bot --kind service --roles operator || true
"$EGR_BIN" identity list || true
# o token aparece uma única vez: capturamos para demonstrar a decisão verificada
TOKEN="$("$EGR_BIN" identity token vitor --ttl-days 7 --label demo 2>&1 | grep '^token:' | cut -d' ' -f2- || true)"
"$EGR_BIN" identity whoami --by "$TOKEN" || true
"$EGR_BIN" security status || true
# sem identidade exigida, a decisão acontece mas fica marcada como não verificada
DEMO_APPROVAL="$("$EGR_BIN" tool test email.send --arg to=financeiro@acme.com --arg subject=demo --arg body=oi --execute 2>&1 | grep -oE 'apr_[A-Za-z0-9_]+' | head -1 || true)"
if [ -n "$DEMO_APPROVAL" ]; then
  "$EGR_BIN" approval approve "$DEMO_APPROVAL" --by vitor --token "$TOKEN" --note "identidade verificada" || true
fi

step "10/13 · Fase 5 — Memória (semântica, híbrida e ciclo de vida)"
"$EGR_BIN" memory write "O limite de aprovação automática de pagamentos é de R$ 5.000,00" \
  --kind knowledge --namespace finance --tags "politica,financeiro" || true
"$EGR_BIN" memory write "Toda despesa precisa de nota fiscal vinculada ao pedido de compra" \
  --kind knowledge --namespace finance || true
"$EGR_BIN" memory write "O limite de aprovacao automatica de pagamentos e de R$ 5.000,00" \
  --kind knowledge --namespace finance || true
echo "→ busca por sentido (não repete nenhuma palavra-chave exata):"
"$EGR_BIN" memory search "liberar pagamento sem aprovacao" -n finance -l 3 --explain || true
"$EGR_BIN" memory stats || true
"$EGR_BIN" memory consolidate || true

step "11/13 · Fase 6 — Orquestração (DAG, retry, agenda)"
"$EGR_BIN" workflow validate || true
"$EGR_BIN" workflow run invoice-processing || true
"$EGR_BIN" workflow runs || true
"$EGR_BIN" workflow schedule || true
"$EGR_BIN" workflow tick || true
"$EGR_BIN" workflow triggers || true

step "12/13 · Fase 7 — Development Environment (proposta verificada)"
"$EGR_BIN" dev status || true

DEMO_TOOL="$WORKSPACE/.egr/dev/demo-tool.py"
mkdir -p "$(dirname "$DEMO_TOOL")"
cat > "$DEMO_TOOL" <<'PY'
"""Ferramenta criada dentro do sandbox pelo próprio Runtime."""

from __future__ import annotations

from egr.domain.enums import RiskLevel
from egr.domain.tool import ToolRequest, ToolResult, ToolSpec
from egr.tools.protocol import Tool


class DemoLinhasTool(Tool):
    spec = ToolSpec(
        name="demo.linhas",
        description="conta as linhas de um texto",
        parameters={"texto": {"type": "string", "required": True}},
        risk=RiskLevel.LOW,
    )

    def execute(self, request: ToolRequest, ctx) -> ToolResult:
        return ToolResult.success({"linhas": len(request.args["texto"].splitlines())})
PY

# um agente (ou humano) propõe — nada é escrito no workspace ainda
OUT=$("$EGR_BIN" dev new tool demo.linhas --from "$DEMO_TOOL" 2>&1) || true
printf '%s\n' "$OUT"
PROPOSAL=$(printf '%s\n' "$OUT" | grep -o "prp_[a-zA-Z0-9_]*" | head -1) || true
if [ -n "$PROPOSAL" ]; then
  # prova isolada, aprovação humana e só então a escrita
  "$EGR_BIN" dev test "$PROPOSAL" --args '{"texto": "uma\nduas"}' || true
  "$EGR_BIN" dev approve "$PROPOSAL" --by demo || true
  "$EGR_BIN" dev apply "$PROPOSAL" --by demo || true
fi
"$EGR_BIN" dev tools || true

step "13/13 · auditoria e memória"
"$EGR_BIN" audit verify
"$EGR_BIN" audit stats
"$EGR_BIN" memory search "documentos" || true
"$EGR_BIN" task list

printf '\n\033[1;32mDemo concluído.\033[0m Workspace: %s\n' "$WORKSPACE"
printf 'Para o console: cd %s && egr serve\n' "$WORKSPACE"
