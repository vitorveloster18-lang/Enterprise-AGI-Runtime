---
name: repo_navigation
description: Graph-first repository navigation for unfamiliar codebases.
---

# repo_navigation

Use when starting any task in a repository the agent has not seen before.
Goal: map the smallest relevant subgraph before reading files.

## Strategy (in order)

1. `search_similar_code` with the error message / feature text (k=10).
2. `get_code_neighbors` on the top 2–3 nodes (callers + callees).
3. `get_code_subgraph` on confirmed symbols to bound the area.
4. `read_file` with line slices only inside the mapped area.
5. `scripts/locate.sh <keyword>` only when symbols are unknown (blind grep
   is the last resort, not the first).

Details: `resources/graph_guide.md`.
