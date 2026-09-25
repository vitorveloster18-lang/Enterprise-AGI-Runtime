#!/bin/bash
# Run focused tests and compress the output to a verdict-friendly summary.
# Usage: run_focused.sh <test-path>...   (defaults to whole suite if empty: AVOID, slow)
set -u
if [ "$#" -eq 0 ]; then
  echo "run_focused.sh: refusing full suite without paths (too slow for the budget)" >&2
  exit 2
fi
python -m pytest "$@" -q -p no:warnings 2>&1 | tail -15
