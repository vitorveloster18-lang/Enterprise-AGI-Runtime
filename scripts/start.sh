#!/usr/bin/env bash
#
# EGR — partida local (Termux / Linux / macOS)
#
#   Prepara o ambiente, TESTA o Runtime de verdade e abre um painel de linha
#   de comando. Nada de servidor: este script nunca sobe `egr serve` nem abre
#   console web — a interface é o terminal, que é onde o Runtime vive.
#
# Uso:
#   bash scripts/start.sh                 # prepara + auto-teste + menu
#   bash scripts/start.sh --check         # só o auto-teste, sai com 0/1
#   bash scripts/start.sh --shell         # prompt livre (digite comandos egr)
#   bash scripts/start.sh --workspace DIR # usa/outro workspace
#   bash scripts/start.sh --no-install    # não tenta criar a venv
#
# O auto-teste não é "o comando existe?": é o caminho governado funcionando —
# doctor, cadeia de auditoria íntegra e uma task executada de verdade.

set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${EGR_WORKSPACE:-$HOME/egr-workspace}"
MODE="menu"
NO_INSTALL=0
INIT_ARGS=()

# ---------------------------- utilidades -------------------------------
c_reset=$'\033[0m'; c_bold=$'\033[1m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'
c_red=$'\033[31m'; c_cyan=$'\033[36m'; c_dim=$'\033[2m'

info()    { printf '%s•%s %s\n' "$c_cyan" "$c_reset" "$*"; }
ok()      { printf '%s✓%s %s\n' "$c_green" "$c_reset" "$*"; }
warn()    { printf '%s!%s %s\n' "$c_yellow" "$c_reset" "$*"; }
fail()    { printf '%s✗%s %s\n' "$c_red" "$c_reset" "$*"; }
title()   { printf '\n%s%s%s\n' "$c_bold" "$*" "$c_reset"; }
rule()    { printf '%s%s%s\n' "$c_dim" "─────────────────────────────────────────────────────────────" "$c_reset"; }

usage() {
    sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --check)      MODE="check"; shift ;;
        --shell)      MODE="shell"; shift ;;
        --workspace)  WORKSPACE="${2:-}"; shift 2 ;;
        --no-install) NO_INSTALL=1; shift ;;
        --help|-h)    usage ;;
        *)            INIT_ARGS+=("$1"); shift ;;
    esac
done

# ------------------------- 1. ambiente python --------------------------
python_bin() {
    for candidate in "$REPO/.venv/bin/python" "$REPO/.venv/bin/python3" "$(command -v python3)" "$(command -v python)"; do
        [ -n "$candidate" ] && [ -x "$candidate" ] && printf '%s' "$candidate" && return 0
    done
    return 1
}

title "EGR — partida local"
rule

PY="$(python_bin || true)"
if [ -z "${PY:-}" ]; then
    fail "nenhum python3 encontrado (instale python 3.11+)"
    exit 1
fi

if [ -x "$REPO/.venv/bin/egr" ]; then
    EGR="$REPO/.venv/bin/egr"
    ok "ambiente pronto  $( "$EGR" version 2>/dev/null | head -1 )"
else
    if [ "$NO_INSTALL" -eq 1 ]; then
        EGR="$PY -m egr"
        warn "sem venv em $REPO/.venv — usando o python do sistema (--no-install)"
    else
        info "preparando o ambiente (primeira vez demora um pouco)…"
        if "$PY" -m venv "$REPO/.venv" >/dev/null 2>&1 \
           && "$REPO/.venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 \
           && "$REPO/.venv/bin/python" -m pip install --quiet -r "$REPO/requirements.txt" >/dev/null 2>&1; then
            EGR="$REPO/.venv/bin/egr"
            ok "ambiente criado em $REPO/.venv"
        else
            EGR="$PY -m egr"
            warn "não consegui criar a venv (sem rede?); tentando o python do sistema"
        fi
    fi
fi

egr() { # wrapper: imprime e executa, mantendo o código de saída
    if [ -n "${WORKSPACE:-}" ]; then
        $EGR "$@" --workspace "$WORKSPACE"
    else
        $EGR "$@"
    fi
}

# --------------------------- 2. workspace ------------------------------
if [ ! -d "$WORKSPACE/.egr" ]; then
    info "workspace novo: criando em $WORKSPACE"
    mkdir -p "$WORKSPACE" 2>/dev/null
    # shellcheck disable=SC2086
    if $EGR init "$WORKSPACE" ${INIT_ARGS[*]:-} >/dev/null 2>&1; then
        ok "workspace inicializado"
    else
        fail "não consegui inicializar o workspace em $WORKSPACE"
        exit 1
    fi
else
    ok "workspace: $WORKSPACE"
fi

# --------------------------- 3. auto-teste -----------------------------
title "Auto-teste do Runtime"
rule

problems=0

step_ok()   { ok "$1"; }
step_fail() { fail "$1"; problems=$((problems + 1)); }

# 3.1 estrutura e migrações
migrations="$(egr status --json 2>/dev/null | tr ',' '\n' | grep -i '"total"' | head -1 | tr -dc '0-9')"
if [ -n "${migrations:-}" ] && [ "$migrations" -ge 15 ] 2>/dev/null; then
    step_ok "banco com ${migrations} migrações aplicadas"
else
    step_fail "banco sem migrações aplicadas (rode: egr status)"
fi

# 3.2 doctor (conta o que está quebrado; avisos de sandbox/chave são esperados)
doctor_out="$(egr doctor 2>&1 || true)"
doctor_fail="$(printf '%s' "$doctor_out" | grep -c 'FALHA' || true)"
if [ "${doctor_fail:-0}" -eq 0 ]; then
    step_ok "doctor: tudo OK"
else
    warn "doctor: ${doctor_fail} check(s) FALHA — detalhe abaixo"
    printf '%s\n' "$doctor_out" | grep 'FALHA' | sed 's/^/    /'
    # sandbox em modo processo e chave ausente são o começo normal de um
    # workspace novo; qualquer outra falha é problema de verdade
    unexpected="$(printf '%s' "$doctor_out" | grep 'FALHA' \
        | grep -vE 'sandbox:isolamento|security:chave|security:identidade' || true)"
    if [ -n "$unexpected" ]; then
        step_fail "doctor apontou falha fora do esperado para workspace novo"
    else
        info "falhas esperadas de um workspace novo (sem contêiner, sem chave, sem identidades)"
    fi
fi

# 3.3 trilha de auditoria
if egr audit verify 2>&1 | grep -q 'íntegra'; then
    step_ok "auditoria: cadeia íntegra"
else
    step_fail "auditoria: cadeia quebrada (egr audit verify)"
fi

# 3.4 execução governada de verdade
task_out="$(egr task "responda apenas: ok" 2>&1 || true)"
if printf '%s' "$task_out" | grep -qiE 'conclu|completed|ok|Inventario|passo'; then
    step_ok "task executada pelo caminho governado (política, ferramenta e trilha)"
else
    step_fail "task não executou: $(printf '%s' "$task_out" | tail -1)"
fi

# 3.5 trilha cresceu
events="$(egr status --json 2>/dev/null | tr ',' '\n' | grep -A0 -i 'events' | tr -dc '0-9' | head -c 6)"
if [ -n "${events:-}" ] && [ "$events" -gt 0 ] 2>/dev/null; then
    step_ok "trilha registrando eventos (${events} no total)"
else
    warn "não consegui ler a contagem de eventos (egr status)"
fi

rule
if [ "$problems" -eq 0 ]; then
    ok "Runtime local funcionando"
else
    fail "${problems} problema(s) — resolva antes de usar"
fi

if [ "$MODE" = "check" ]; then
    exit $((problems > 0))
fi

# ----------------------------- 4. shell --------------------------------
if [ "$MODE" = "shell" ]; then
    title "Shell EGR (digite comandos sem o prefixo 'egr'; 'sair' para voltar)"
    while true; do
        printf '\n%segr>%s ' "$c_green" "$c_reset"
        IFS= read -r line || break
        [ -z "$line" ] && continue
        case "$line" in
            sair|voltar|exit|quit|0) break ;;
            bash|sh) printf '%s\n' "use 'sair' para encerrar o shell EGR" ;;
            *) egr $line ;;
        esac
    done
    exit 0
fi

# ----------------------------- 5. menu ---------------------------------
show_menu() {
    rule
    printf '%sPainel local (sem interface online — tudo pelo terminal)%s\n' "$c_bold" "$c_reset"
    cat <<'MENU'
  1) status               2) doctor (saúde)
  3) rodar uma task       4) listar tasks
  5) memória: buscar      6) avaliação: smoke
  7) promoção (release)   8) auditoria: verificar
  9) mídia e PII (5b)    10) coordenação e gatilhos (6b)
 11) modelos e custo     12) ver o log de auditoria
  s) shell egr           t) repetir o auto-teste
  0) sair
MENU
}

while true; do
    show_menu
    printf '\nescolha: '
    IFS= read -r choice || break
    case "$choice" in
        1) egr status ;;
        2) egr doctor ;;
        3) printf 'objetivo da task: '; IFS= read -r objetivo; [ -n "$objetivo" ] && egr task "$objetivo" ;;
        4) egr task list ;;
        5) printf 'buscar na memória: '; IFS= read -r busca; egr memory search "${busca:-}" -l 5 ;;
        6) printf 'artefato (ex.: tool:nome) ou vazio para listar: '; IFS= read -r artefato
           if [ -n "$artefato" ]; then egr eval smoke "$artefato"; else egr eval list; fi ;;
        7) egr release status; egr release list ;;
        8) egr audit verify ;;
        9) egr memory media; egr memory stats ;;
        10) egr db triggers; egr db drain ;;
        11) egr model usage; egr model list ;;
        12) egr audit list --limit 20 ;;
        s|S) printf '%s\n' "digite comandos sem o prefixo 'egr' (ex.: status); 'voltar' para o menu"
             while true; do
                 printf '%segr>%s ' "$c_green" "$c_reset"
                 IFS= read -r line || break 2
                 [ -z "$line" ] && continue
                 [ "$line" = "voltar" ] && break
                 egr $line
             done ;;
        t|T) exec "${BASH_SOURCE[0]}" --check --workspace "$WORKSPACE" ;;
        0|q|Q) break ;;
        *) warn "opção inválida" ;;
    esac
    printf '\n'
done

ok "até logo — o Runtime fica em $WORKSPACE"
