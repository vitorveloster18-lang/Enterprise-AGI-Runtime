# Test-evidence standards

- **Relevant > many.** Run the tests that cover the changed area, not the
  whole suite (the 12h budget is shared across all tasks).
- **Report raw outcomes.** `3 passed`, `1 failed: test_x`, `collection error:
  ...` — never paraphrase a failure away.
- **A failing patch with an honest report outranks a silent patch.** The
  reviewer can only fix what it can see.
- **Never edit tests to make them pass** (unless the task explicitly asks).
  A patch that games the tests is a failure, even if the suite is green.
- **No tests ≠ no evidence.** If nothing runs, write one line saying why
  (no tests found, infra broken, timeout) so the orchestrator can decide.
