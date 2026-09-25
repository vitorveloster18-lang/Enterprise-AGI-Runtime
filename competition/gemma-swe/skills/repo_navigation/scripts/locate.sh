#!/bin/bash
# Blind keyword search inside /workspace. Last resort: prefer the graph tools.
# Usage: locate.sh <keyword> [glob]
set -u
KEYWORD="${1:?usage: locate.sh <keyword> [glob]}"
GLOB="${2:-*.py}"
grep -rn --include="$GLOB" -I "$KEYWORD" . 2>/dev/null | head -30
