#!/usr/bin/env bash
# Diagnóstico de instalação do EGR no Termux/Android.
# Não instala nada e não precisa de root: só olha o ambiente e imprime um relatório.
# Uso:  bash scripts/diagnose.sh  [--dir /caminho/do/egr]
set -u

DIR="${1:-}"
if [ "$DIR" = "--dir" ]; then DIR="${2:-}"; fi

say()  { printf '%s\n' "$*"; }
hdr()  { printf '\n── %s ─────────────────────────────\n' "$*"; }
ok()   { printf '[ok]   %s\n' "$*"; }
bad()  { printf '[FALTA] %s\n' "$*"; }
warn() { printf '[atenção] %s\n' "$*"; }

hdr "1. ambiente"
say "data: $(date)"
say "kernel: $(uname -a 2>/dev/null | cut -c1-90)"
if [ -n "${PREFIX:-}" ]; then
    ok "rodando no Termux (PREFIX=$PREFIX)"
else
    warn "não parece Termux (PREFIX vazio) — pode ser Linux comum, tudo bem"
fi
say "HOME=$HOME"
say "diretório atual: $(pwd)"
say "shell: ${SHELL:-?} · bash $BASH_VERSION"

hdr "2. ferramentas"
for c in python python3 git unzip curl; do
    if p=$(command -v "$c" 2>/dev/null); then
        v=""
        case "$c" in
            python|python3) v=$("$p" --version 2>&1 | head -1) ;;
        esac
        ok "$c → $p ${v}"
    else
        bad "$c não encontrado"
    fi
done

hdr "3. onde está a pasta do EGR"
# lugares onde o arquivo costuma parar depois de baixar/extrair
CANDIDATES=""
for base in "$HOME" "$HOME/storage/downloads" "$HOME/storage/shared/Download" \
            "$HOME/downloads" "$HOME/Downloads" "/sdcard/Download" "/sdcard/Downloads" "$(pwd)"; do
    [ -n "$base" ] && [ -d "$base" ] && CANDIDATES="$CANDIDATES $base"
done

# procura a pasta que tem scripts/start.sh — aceitando uma camada extra de
# aninhamento (acontece quando o zip é extraído dentro de outra pasta igual)
locate_start() {
    find "$1" -maxdepth 5 -type f -path '*/scripts/start.sh' 2>/dev/null | head -1
}

if [ -n "$DIR" ]; then                 # --dir manda: confia no caminho dado
    FOUND="$DIR"
else
    FOUND=""
    for base in $CANDIDATES; do
        hit=$(locate_start "$base")
        if [ -n "$hit" ]; then
            FOUND="${hit%/scripts/start.sh}"
            break
        fi
    done
fi
if [ -n "$FOUND" ] && [ ! -f "$FOUND/scripts/start.sh" ]; then
    hit=$(locate_start "$FOUND")
    [ -n "$hit" ] && FOUND="${hit%/scripts/start.sh}"
fi

if [ -z "$FOUND" ]; then
    bad "não achei scripts/start.sh em nenhum lugar conhecido"
    say "procurei em:$CANDIDATES"
    say "onde você extraiu o zip? rode:  ls -la"
    exit 1
fi

say "encontrei: $FOUND"
case "$FOUND" in
    /sdcard/*|*/storage/*|*/shared/*)
        warn "a pasta está na área compartilhada do Android (noexec):"
        say "     scripts não executam dentro de /sdcard. Mova para a home:"
        say "     cp -r \"$FOUND\" ~/Enterprise-AGI-Runtime && cd ~/Enterprise-AGI-Runtime"
        ;;
    "$HOME"/*) ok "dentro da home (correto — aqui executa)" ;;
    *)         warn "fora da home; se der 'Permission denied', mova para ~" ;;
esac

cd "$FOUND" || exit 1
say ""
say "conteúdo da pasta:"
ls -1 | head -20 | sed 's/^/  /'

# pasta duplicada depois de extrair (zip dentro de zip / extração aninhada)
INNER=$(find "$FOUND" -maxdepth 2 -type d -name 'Enterprise-AGI-Runtime*' 2>/dev/null | grep -v "^$FOUND\$" | head -1)
if [ -n "$INNER" ]; then
    warn "existe uma pasta repetida dentro: $INNER"
    say "     use a de dentro:  cd \"$INNER\""
fi

for f in scripts/start.sh scripts/install-alias.sh pyproject.toml requirements.txt; do
    if [ -f "$f" ]; then ok "$f existe"; else bad "$f NÃO existe (extração incompleta?)"; fi
done
if [ -d src/egr ]; then ok "src/egr existe"; else bad "src/egr NÃO existe (zip errado?)"; fi

hdr "4. comando E"
if p=$(command -v E 2>/dev/null); then
    ok "E encontrado: $p"
elif [ -x "$HOME/bin/E" ]; then
    warn "~/bin/E existe mas não está no PATH — rode: export PATH=\"\$HOME/bin:\$PATH\""
elif grep -q "alias E=" "$HOME/.bashrc" 2>/dev/null || grep -q "alias E=" "$HOME/.zshrc" 2>/dev/null; then
    warn "o alias está no rc, mas este shell não o carregou — rode: source ~/.bashrc"
else
    bad "E não instalado — rode: bash scripts/install-alias.sh"
fi

hdr "5. venv"
if [ -x "$FOUND/.venv/bin/python" ]; then
    ok "$FOUND/.venv existe"
    say "  $($FOUND/.venv/bin/python --version 2>&1)"
    if [ -x "$FOUND/.venv/bin/egr" ]; then
        ok "  comando egr instalado na venv"
    else
        bad "  venv existe mas o comando egr NÃO foi instalado dentro dela"
    fi
else
    say "  (nenhuma venv ainda — o start.sh cria na primeira execução)"
fi

hdr "6. executando o auto-teste (pode levar 1 min na primeira vez)"
OUT=$(cd "$FOUND" && timeout 300 bash scripts/start.sh --check 2>&1)
CODE=$?
say "$OUT" | sed 's/^/  /'
say ""
if [ "$CODE" = "0" ]; then
    ok "auto-teste passou (exit 0)"
else
    bad "auto-teste saiu com exit $CODE"
fi

hdr "como me mandar"
say "copie tudo acima (principalmente a seção 6) e cole na conversa."
