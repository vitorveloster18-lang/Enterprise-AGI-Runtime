# Graph tools guide

The harness precomputes a call/dependency graph with embeddings. It is the
fastest way to understand an unfamiliar repo — use it before reading files.

- `search_similar_code(query, k=10)`: semantic entry point. Best queries are
  error messages, exception names, or feature nouns from the task. Returns
  graph nodes, not file text.
- `get_code_neighbors(node, edge_type?, max_neighbors=50)`: expands one node
  into callers and callees. Use it to find who breaks if you change a symbol,
  and who to read to understand it. Cap at 50; narrow with `edge_type` when
  the neighborhood is noisy.
- `get_code_subgraph(nodes)`: induced subgraph (nodes + interconnecting
  edges) for a confirmed symbol list. Use it to bound the change area and to
  brief the coder: the subgraph IS the task context.

Anti-patterns: reading whole files before mapping; expanding more than ~10
neighbors per node without a question; trusting similar-code hits without
confirming via neighbors (similarity is a hint, the graph is evidence).
