#!/usr/bin/env bash
# End-to-end demo of the Enterprise AGI Runtime (Fase 0 + Fase 1).
#
#   init -> status -> doctor -> task (dev) -> task (production, approval)
#   -> approval approve -> audit verify -> memory search
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

step "1/9 · init"
"$EGR_BIN" init "$WORKSPACE" --enterprise acme --name "ACME Contabilidade" --force

cd "$WORKSPACE"

step "2/9 · status"
"$EGR_BIN" status

step "3/9 · doctor"
"$EGR_BIN" doctor || true

step "4/9 · políticas e agentes declarativos"
"$EGR_BIN" agent sync
"$EGR_BIN" policy sync
"$EGR_BIN" policy test payment.create --arg amount=9000 || true

step "5/9 · primeira task autônoma local (milestone)"
"$EGR_BIN" task "Analise os documentos desta pasta e produza um relatório." --agent document-agent || true

step "6/9 · task em produção pausa para aprovação humana"
set +e
OUTPUT="$("$EGR_BIN" task "Gerar relatório consolidado do mês" --env production 2>&1)"
echo "$OUTPUT"
set -e
APPROVAL="$(echo "$OUTPUT" | grep -oE 'apr_[A-Za-z0-9_]+' | head -1 || true)"

if [ -n "$APPROVAL" ]; then
  step "7/9 · aprovação humana ($APPROVAL)"
  "$EGR_BIN" approval approve "$APPROVAL" --by "demo" --note "aprovado no demo"
else
  step "7/9 · nenhuma aprovação pendente"
fi

step "8/9 · Fase 3 — Tool Runtime (sandbox, git, MCP)"
"$EGR_BIN" tool list
"$EGR_BIN" policy test git.commit || true
"$EGR_BIN" tool test git.status --execute || true
"$EGR_BIN" mcp list || true
"$EGR_BIN" mcp call mcp.calculadora.somar --arg a=40 --arg b=2 --execute || true

step "9/9 · auditoria e memória"
"$EGR_BIN" audit verify
"$EGR_BIN" audit stats
"$EGR_BIN" memory search "documentos" || true
"$EGR_BIN" task list

printf '\n\033[1;32mDemo concluído.\033[0m Workspace: %s\n' "$WORKSPACE"
printf 'Para o console: cd %s && egr serve\n' "$WORKSPACE"
