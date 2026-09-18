#!/usr/bin/env bash
# End-to-end demo of the Enterprise AGI Runtime (Fase 0 + Fase 1).
#
#   init -> status -> doctor -> task (dev) -> task (production, approval)
#   -> approval approve -> tools/MCP -> segurança (identidade, RBAC, cofre)
#   -> orquestração -> desenvolvimento -> avaliação -> release -> canais
#   -> integrações -> packs verticais -> audit verify -> memory search
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

step "1/16 · init"
"$EGR_BIN" init "$WORKSPACE" --enterprise acme --name "ACME Contabilidade" --force

cd "$WORKSPACE"

step "2/16 · status"
"$EGR_BIN" status

step "3/16 · doctor"
"$EGR_BIN" doctor || true

step "4/16 · políticas e agentes declarativos"
"$EGR_BIN" agent sync
"$EGR_BIN" policy sync
"$EGR_BIN" policy test payment.create --arg amount=9000 || true

step "5/16 · primeira task autônoma local (milestone)"
"$EGR_BIN" task "Analise os documentos desta pasta e produza um relatório." --agent document-agent || true

step "6/16 · task em produção pausa para aprovação humana"
set +e
OUTPUT="$("$EGR_BIN" task "Gerar relatório consolidado do mês" --env production 2>&1)"
echo "$OUTPUT"
set -e
APPROVAL="$(echo "$OUTPUT" | grep -oE 'apr_[A-Za-z0-9_]+' | head -1 || true)"

if [ -n "$APPROVAL" ]; then
  step "7/16 · aprovação humana ($APPROVAL)"
  "$EGR_BIN" approval approve "$APPROVAL" --by "demo" --note "aprovado no demo"
else
  step "7/16 · nenhuma aprovação pendente"
fi

step "8/16 · Fase 3 — Tool Runtime (sandbox, git, MCP)"
"$EGR_BIN" tool list
"$EGR_BIN" policy test git.commit || true
"$EGR_BIN" tool test git.status --execute || true
"$EGR_BIN" mcp list || true
"$EGR_BIN" mcp call mcp.calculadora.somar --arg a=40 --arg b=2 --execute || true

step "9/16 · Fase 4 — Segurança (identidade, RBAC, cofre, chaves)"
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

step "10/16 · Fase 5 — Memória (semântica, híbrida e ciclo de vida)"
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

step "11/16 · Fase 6 — Orquestração (DAG, retry, agenda)"
"$EGR_BIN" workflow validate || true
"$EGR_BIN" workflow run invoice-processing || true
"$EGR_BIN" workflow runs || true
"$EGR_BIN" workflow schedule || true
"$EGR_BIN" workflow tick || true
"$EGR_BIN" workflow triggers || true

step "12/16 · Fase 7 — Development Environment (proposta verificada)"
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

step "13/16 · Fase 8 — Evaluation (suíte, métricas, veredito)"
"$EGR_BIN" eval smoke workflow invoice-processing || true
"$EGR_BIN" eval list || true
"$EGR_BIN" eval runs || true
"$EGR_BIN" eval security agent document-agent || true

step "14/16 · Fase 9 — Production Governance (release, gates, promoção, rollback)"
"$EGR_BIN" release status || true
RELEASE_ID=$("$EGR_BIN" release create workflow:invoice-processing --to staging --reason "demo" | sed -n 's/.*\(rel_[A-Za-z0-9_]*\).*/\1/p' | head -1)
if [ -n "$RELEASE_ID" ]; then
  "$EGR_BIN" release submit "$RELEASE_ID" || true
  "$EGR_BIN" release approve "$RELEASE_ID" --by human:vitor --note "demo" || true
  "$EGR_BIN" release deploy "$RELEASE_ID" --by human:vitor || true
  "$EGR_BIN" release rollback "$RELEASE_ID" --by human:vitor --note "demo" || true
fi
"$EGR_BIN" release versions workflow invoice-processing || true
"$EGR_BIN" release list || true

step "15/23 · Lacuna 9b — promoção assinada e com quórum"
RELEASE_ID=$("$EGR_BIN" release create workflow:invoice-processing --to staging --reason "demo 9b" | sed -n 's/.*\(rel_[A-Za-z0-9_]*\).*/\1/p' | head -1)
if [ -n "$RELEASE_ID" ]; then
  "$EGR_BIN" release submit "$RELEASE_ID" || true
  # staging: um voto basta. produção exigiria dois, de pessoas diferentes.
  "$EGR_BIN" release approve "$RELEASE_ID" --by human:vitor --note "demo" || true
  "$EGR_BIN" release approvals "$RELEASE_ID" || true
  "$EGR_BIN" release sign "$RELEASE_ID" --by human:vitor || true
  "$EGR_BIN" release verify "$RELEASE_ID" || true
fi

step "16/23 · Fase 10 — Remote Control (gateway, pareamento, mensagem governada)"
# o gateway nasce desligado (default deny): o demo liga para mostrar os dois lados
HABILITADO="$(mktemp)"
awk '/^gateway:/{g=1} g && /^  enabled:/ {print "  enabled: true"; g=0; next} {print}' \
  "$WORKSPACE/egr.yaml" > "$HABILITADO" && mv "$HABILITADO" "$WORKSPACE/egr.yaml"
"$EGR_BIN" gateway status || true
"$EGR_BIN" gateway send web demo "resuma os documentos" || true   # recusada: mostra o código
CODIGO=$("$EGR_BIN" gateway bindings --json 2>/dev/null | sed -n 's/.*"código": "\([A-F0-9]\{6\}\)".*/\1/p' | head -1)
if [ -n "$CODIGO" ]; then
  "$EGR_BIN" gateway pair web demo --code "$CODIGO" --role operator --by human:vitor || true
else
  "$EGR_BIN" gateway pair web demo --role operator --by human:vitor || true
fi
"$EGR_BIN" gateway send web demo "resuma os documentos" || true   # agora executa
"$EGR_BIN" gateway bindings || true
"$EGR_BIN" gateway messages || true

step "17/23 · Fase 11 — Integrações (conector declarado, chamada governada)"
"$EGR_BIN" integration sync
"$EGR_BIN" integration list || true
"$EGR_BIN" integration enable WAREHOUSE --by human:vitor
"$EGR_BIN" integration call WAREHOUSE --query "select count(*) as total from tasks" || true
"$EGR_BIN" integration call WAREHOUSE --query "delete from tasks" || true   # recusada: somente leitura
"$EGR_BIN" integration calls || true
"$EGR_BIN" integration events || true

step "18/23 · Fase 12 — Packs verticais (catálogo, proposta, instalação)"
"$EGR_BIN" pack list || true
"$EGR_BIN" pack show finance || true
"$EGR_BIN" pack check finance || true
PACK=$("$EGR_BIN" pack install finance --by human:vitor --json 2>/dev/null | sed -n 's/.*"id": "\(prp_[a-z0-9_]*\)".*/\1/p' | head -1)
if [ -n "$PACK" ]; then
  "$EGR_BIN" proposal approve "$PACK" --by human:vitor || true
  "$EGR_BIN" proposal apply "$PACK" --by human:vitor || true
fi
"$EGR_BIN" pack status || true

step "19/23 · Fase 12 — Fila de saída (promessa, espera crescente, desistência)"
"$EGR_BIN" integration enable WAREHOUSE --by human:vitor || true
"$EGR_BIN" integration enqueue WAREHOUSE "" --query "select count(*) as total from tasks" -k demo-fila || true
"$EGR_BIN" integration jobs || true
"$EGR_BIN" integration drain || true
"$EGR_BIN" integration jobs || true

step "20/23 · Lacuna 10b — anexos entram governados, botões viram comando"
printf 'cliente: ACME\nvalor: 1200,00\nvencimento: 2026-10-01\n' > "$WORKSPACE/entrada.txt"
printf 'MZ\x00' > "$WORKSPACE/suspeito.exe"
"$EGR_BIN" gateway upload web demo "$WORKSPACE/entrada.txt" --text "classifique este anexo" || true
"$EGR_BIN" gateway upload web demo "$WORKSPACE/suspeito.exe" || true   # recusado: tipo fora da lista
"$EGR_BIN" gateway attachments || true
ANEXO=$("$EGR_BIN" gateway attachments --status stored --json 2>/dev/null | sed -n 's/.*"id": "\(att_[a-z0-9_]*\)".*/\1/p' | head -1)
CAMINHO=$("$EGR_BIN" gateway attachments --json 2>/dev/null | sed -n 's/.*"caminho": "\(artifacts[^"]*\)".*/\1/p' | head -1)
if [ -n "$ANEXO" ]; then
  "$EGR_BIN" gateway attachment "$ANEXO" || true
fi
if [ -n "$CAMINHO" ]; then
  "$EGR_BIN" gateway send-file web demo "$CAMINHO" || true
fi
"$EGR_BIN" gateway interact web demo ajuda || true

step "21/23 · Lacuna 12b — worker da fila e atualização de pack"
"$EGR_BIN" integration enqueue WAREHOUSE "" --query "select 1 as ok" -k demo-worker || true
"$EGR_BIN" integration worker --interval 1 --rounds 2 || true
"$EGR_BIN" integration jobs || true
# uma versão nova no catálogo do workspace: atualização preserva o que foi editado aqui
if [ -f "$WORKSPACE/agents/cashflow-agent.yaml" ]; then
  printf '\n# ajuste local do demo\n' >> "$WORKSPACE/agents/cashflow-agent.yaml"
  sed -i.bak 's/^version: 1\.0\.0$/version: 1.1.0/' "$WORKSPACE/packs/finance.yaml" && rm -f "$WORKSPACE/packs/finance.yaml.bak"
  "$EGR_BIN" pack update finance --by human:vitor || true
  UPDATE=$("$EGR_BIN" pack update finance --json --by human:vitor 2>/dev/null | sed -n 's/.*"id": "\(prp_[a-z0-9_]*\)".*/\1/p' | head -1)
  if [ -n "$UPDATE" ]; then
    "$EGR_BIN" proposal approve "$UPDATE" --by human:vitor || true
    "$EGR_BIN" proposal apply "$UPDATE" --by human:vitor || true
  fi
  "$EGR_BIN" pack status || true
fi

step "22/24 · Lacuna 6b — coordenação negociada e gatilhos de banco"
"$EGR_BIN" task negotiate "conciliar lançamentos do dia" || true
"$EGR_BIN" db trigger-add "task falhou" --on tasks --event update \
  --when "NEW.status = 'failed'" --emit db.task_failed || true
"$EGR_BIN" db trigger-add "task criada" --on tasks --event insert \
  --when "NEW.status = 'pending'" --emit db.task_created || true
"$EGR_BIN" db triggers || true
# uma task nova faz o gatilho de insert avisar; o dreno transforma em evento
"$EGR_BIN" task create "demo de gatilho de banco" --agent finance-agent || true
"$EGR_BIN" db events || true
"$EGR_BIN" db drain || true
"$EGR_BIN" db events || true

step "23/24 · Lacuna 8b — laboratório (qualidade e carga)"
SUITE=$("$EGR_BIN" eval list --json 2>/dev/null | sed -n 's/.*"id": "\([a-z0-9._-]*\)".*/\1/p' | head -1)
if [ -n "$SUITE" ]; then
  "$EGR_BIN" eval judge "$SUITE" --method similaridade || true
  "$EGR_BIN" eval compare "$SUITE" --models echo || true
  "$EGR_BIN" eval load "$SUITE" --requests 6 --concurrency 3 || true
  "$EGR_BIN" eval loads || true
else
  echo "nenhuma suíte registrada ainda"
fi

step "24/24 · auditoria e memória"
"$EGR_BIN" audit verify
"$EGR_BIN" audit stats
"$EGR_BIN" memory search "documentos" || true
"$EGR_BIN" task list

printf '\n\033[1;32mDemo concluído.\033[0m Workspace: %s\n' "$WORKSPACE"
printf 'Para o console: cd %s && egr serve\n' "$WORKSPACE"
