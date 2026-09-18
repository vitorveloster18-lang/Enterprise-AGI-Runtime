#!/usr/bin/env bash
#
# Instala o atalho de partida do EGR no shell (Termux, Linux ou macOS).
#
#   bash scripts/install-alias.sh          # cria o alias KE
#   bash scripts/install-alias.sh E        # cria o alias E
#   bash scripts/install-alias.sh --remove KE
#
# O que ele faz: escreve no arquivo de inicialização do shell uma linha como
#
#     alias KE='bash "/caminho/do/repositorio/scripts/start.sh"'
#
# ...sem subir servidor nenhum: `KE` abre o painel de linha de comando local
# (auto-teste + menu). Nada de interface online.

set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
START="$REPO/scripts/start.sh"
NAME="${1:-KE}"
REMOVE=0
[ "${1:-}" = "--remove" ] && { REMOVE=1; NAME="${2:-KE}"; }

# qual arquivo de inicialização (Termux usa ~/.bashrc; zsh usa ~/.zshrc)
if [ -n "${ZDOTDIR:-}" ] && [ -d "${ZDOTDIR:-}" ] && [ -n "${ZSH_VERSION:-}" ]; then
    RC="${ZDOTDIR}/.zshrc"
elif [ -n "${BASH_VERSION:-}" ] || [ ! -f "$HOME/.zshrc" ]; then
    RC="$HOME/.bashrc"
    # no Termux o bashrc existe; se não existir, cria
else
    RC="$HOME/.zshrc"
fi

LINE="alias ${NAME}='bash \"${START}\"'"
MARKER="# EGR — partida local (gerado por scripts/install-alias.sh)"

if [ "$REMOVE" -eq 1 ]; then
    if [ -f "$RC" ] && grep -qF "$MARKER" "$RC"; then
        cp "$RC" "$RC.bak.egr"
        grep -vF "$MARKER" "$RC" | grep -vF "alias ${NAME}=" > "$RC.tmp" && mv "$RC.tmp" "$RC"
        printf 'removido: alias %s de %s (backup em %s.bak.egr)\n' "$NAME" "$RC" "$RC"
    else
        printf 'nada a remover em %s\n' "$RC"
    fi
    exit 0
fi

touch "$RC"
if grep -qF "alias ${NAME}=" "$RC"; then
    printf 'já existe um alias %s em %s — nada a fazer\n' "$NAME" "$RC"
    printf 'para usar agora: source %s\n' "$RC"
    exit 0
fi

cp "$RC" "$RC.bak.egr"
{
    printf '\n%s\n' "$MARKER"
    printf '%s\n' "$LINE"
    printf '# KE --check   → só roda o auto-teste e sai (0 = ok, 1 = problema)\n'
} >> "$RC"

printf '\n'
printf 'alias criado em %s:\n' "$RC"
printf '  %s\n' "$LINE"
printf '\npara usar agora, rode:\n'
printf '  source %s\n' "$RC"
printf '  %s\n' "$NAME"
printf '\n(backup do arquivo anterior em %s.bak.egr)\n' "$RC"
