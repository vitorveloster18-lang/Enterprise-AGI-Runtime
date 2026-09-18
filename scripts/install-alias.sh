#!/usr/bin/env bash
#
# Instala o atalho de partida do EGR no shell (Termux, Linux ou macOS).
#
#   bash scripts/install-alias.sh          # cria o alias E
#   bash scripts/install-alias.sh KR       # cria outro nome, se preferir
#   bash scripts/install-alias.sh --remove E
#
# O que ele faz: escreve no arquivo de inicialização do shell uma linha como
#
#     alias E='bash "/caminho/do/repositorio/scripts/start.sh"'
#
# ...sem subir servidor nenhum: `E` abre o painel de linha de comando local
# (auto-teste + menu). Nada de interface online.

set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
START="$REPO/scripts/start.sh"
NAME="${1:-E}"
REMOVE=0
[ "${1:-}" = "--remove" ] && { REMOVE=1; NAME="${2:-E}"; }

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

# --- 1. executável em ~/bin: funciona em qualquer shell (até não interativo) ---
BIN="$HOME/bin"
mkdir -p "$BIN"
SHIM="$BIN/$NAME"
printf '#!/usr/bin/env bash\nexec bash "%s" "$@"\n' "$START" > "$SHIM"
chmod +x "$SHIM"

# --- 2. alias no rc do shell (atalho de digitação no shell interativo) --------
touch "$RC"
if ! grep -qF "alias ${NAME}=" "$RC"; then
    cp "$RC" "$RC.bak.egr"
    {
        printf '\n%s\n' "$MARKER"
        printf '%s\n' "$LINE"
    } >> "$RC"
    backup_msg="backup do rc anterior em $RC.bak.egr"
else
    backup_msg="o rc já tinha um alias $NAME (mantido)"
fi

# --- 3. garante ~/bin no PATH ------------------------------------------------
if ! printf '%s' "$PATH" | grep -q "$BIN"; then
    if ! grep -qF 'export PATH="$HOME/bin:$PATH"' "$RC"; then
        printf '\nexport PATH="$HOME/bin:$PATH"\n' >> "$RC"
    fi
    path_msg="adicionei ~/bin ao PATH (vale no próximo shell)"
else
    path_msg="~/bin já está no PATH"
fi

printf '\n'
printf 'comando criado: %s\n' "$NAME"
printf '  executável: %s\n' "$SHIM"
printf '  alias:      %s\n' "$LINE"
printf '  %s\n' "$backup_msg"
printf '  %s\n' "$path_msg"
printf '\npara usar agora:\n'
printf '  source %s && %s\n' "$RC" "$NAME"
printf '  %s --check            # só o auto-teste (0 = ok, 1 = problema)\n' "$NAME"
