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
WORKSPACE="${1:-$ROOT/examples/acme-workspace}"

if [ ! -x "$EGR_BIN" ]; then
  echo "egr não encontrado em $EGR_BIN — rode 'make setup' primeiro."
  exit 1
fi

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

step "1/8 · init"
"$EGR_BIN" init "$WORKSPACE" --enterprise acme --name "ACME Contabilidade" --force

cd "$WORKSPACE"

step "2/8 · status"
"$EGR_BIN" status

step "3/8 · doctor"
"$EGR_BIN" doctor || true

step "4/8 · políticas e agentes declarativos"
"$EGR_BIN" agent sync
"$EGR_BIN" policy sync
"$EGR_BIN" policy test payment.create --arg amount=9000 || true

step "5/8 · primeira task autônoma local (milestone)"
"$EGR_BIN" task "Analise os documentos desta pasta e produza um relatório." --agent document-agent || true

step "6/8 · task em produção pausa para aprovação humana"
set +e
OUTPUT="$("$EGR_BIN" task "Gerar relatório consolidado do mês" --env production 2>&1)"
echo "$OUTPUT"
set -e
APPROVAL="$(echo "$OUTPUT" | grep -oE 'apr_[A-Za-z0-9_]+' | head -1 || true)"

if [ -n "$APPROVAL" ]; then
  step "7/8 · aprovação humana ($APPROVAL)"
  "$EGR_BIN" approval approve "$APPROVAL" --by "demo" --note "aprovado no demo"
else
  step "7/8 · nenhuma aprovação pendente"
fi

step "8/8 · auditoria e memória"
"$EGR_BIN" audit verify
"$EGR_BIN" audit stats
"$EGR_BIN" memory search "documentos" || true
"$EGR_BIN" task list

printf '\n\033[1;32mDemo concluído.\033[0m Console: cd %s && egr serve\n' "$WORKSPACE"
