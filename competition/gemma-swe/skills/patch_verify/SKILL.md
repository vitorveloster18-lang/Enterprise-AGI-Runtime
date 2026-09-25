---
name: patch_verify
description: Standard test-evidence procedure for candidate patches.
---

# patch_verify

Use after every code change, before reporting the patch. No patch is "done"
without evidence from this procedure or a written reason it could not run.

## Procedure

1. Identify covering tests (from the analyzer report, or
   `scripts/run_focused.sh <test-path>...` discovery via the repo layout).
2. Run them: `scripts/run_focused.sh <paths>`.
3. Report: command + pass/fail + failing test names. A fail is data, not
   shame — report it exactly.
4. If the repo has no usable tests: say so explicitly and submit the
   smallest plausible fix (the orchestrator decides).

Evidence standards: `resources/verify_guide.md`.
