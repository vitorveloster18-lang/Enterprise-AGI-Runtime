"""Grafo de código do dataset (formato JSON primeiro; demais após inspeção).

O dataset traz `graphs/` e `embeddings/` — os formatos exatos só aparecem
depois do download. Rode `inspect.py` e mande a saída: o adaptador é
completado a partir dela. O que já funciona: vizinhanças em JSON
`{node: {"in": [...], "out": [...]}}` ou lista de arestas.
"""

from __future__ import annotations

import json
import math
from pathlib import Path


class GraphStore:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self._neighbors: dict[str, dict[str, list[str]]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        graphs = self.root / "graphs"
        if not graphs.is_dir():
            return
        for path in sorted(graphs.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            self._merge(data)

    def _merge(self, data) -> None:
        if isinstance(data, dict) and "edges" in data:
            for edge in data["edges"]:
                src = str(edge.get("source", edge.get("from", "")))
                dst = str(edge.get("target", edge.get("to", "")))
                if src and dst:
                    self._link(src, dst)
        elif isinstance(data, dict):
            for node, info in data.items():
                if isinstance(info, dict):
                    for caller in info.get("in", info.get("callers", [])):
                        self._link(str(caller), str(node))
                    for callee in info.get("out", info.get("callees", [])):
                        self._link(str(node), str(callee))

    def _link(self, src: str, dst: str) -> None:
        self._neighbors.setdefault(src, {"in": [], "out": []})
        self._neighbors.setdefault(dst, {"in": [], "out": []})
        if dst not in self._neighbors[src]["out"]:
            self._neighbors[src]["out"].append(dst)
        if src not in self._neighbors[dst]["in"]:
            self._neighbors[dst]["in"].append(src)

    def neighbors(
        self, node: str, edge_type: str | None = None, max_neighbors: int = 50
    ) -> dict:
        self._load()
        info = self._neighbors.get(node, {"in": [], "out": []})
        _ = edge_type  # filtrado quando o formato trouxer tipos de aresta
        return {
            "node": node,
            "in": info["in"][:max_neighbors],
            "out": info["out"][:max_neighbors],
        }

    def subgraph(self, nodes: list[str]) -> dict:
        self._load()
        wanted = set(nodes)
        edges = []
        for node in wanted:
            info = self._neighbors.get(node, {"in": [], "out": []})
            for callee in info["out"]:
                if callee in wanted:
                    edges.append([node, callee])
        return {"nodes": sorted(wanted), "edges": edges}

    def search_similar(self, query: str, k: int = 10) -> dict:
        """Busca lexical de fallback (embeddings reais após a inspeção)."""
        self._load()
        terms = {term.lower() for term in query.replace("_", " ").split() if len(term) > 2}
        scored = []
        for node in self._neighbors:
            parts = set(node.lower().replace("_", " ").replace(".", " ").split())
            overlap = len(terms & parts)
            if overlap:
                scored.append((overlap, node))
        scored.sort(reverse=True)
        return {
            "query": query,
            "hits": [node for _, node in scored[:k]],
            "note": "fallback lexical; embeddings do dataset entram após inspect.py",
        }

    @staticmethod
    def cosine(left: list[float], right: list[float]) -> float:
        dot = sum(a * b for a, b in zip(left, right))
        norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
        return dot / norm if norm else 0.0
