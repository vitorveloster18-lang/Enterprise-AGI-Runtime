"""As 9 ferramentas da plataforma, reimplementadas para o loop local."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .graph import GraphStore

OUTPUT_CAP = 8000


@dataclass
class Budget:
    started: float = field(default_factory=time.time)
    turns: int = 0
    patches: int = 0
    task_id: str = ""

    def status(self, llm=None) -> dict:
        return {
            "task": self.task_id,
            "elapsed_s": round(time.time() - self.started, 1),
            "turns": self.turns,
            "patches": self.patches,
            "tokens": llm.total_tokens if llm else 0,
        }


def tool_schemas() -> list[dict]:
    def schema(name: str, desc: str, props: dict, required: list[str]) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        }

    return [
        schema(
            "read_file",
            "Read a file in /workspace with 1-indexed inclusive line slicing.",
            {
                "filepath": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            ["filepath"],
        ),
        schema(
            "edit_file",
            "Replace old_string with new_string in an existing non-empty file.",
            {
                "filepath": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "allow_multiple": {"type": "boolean"},
            },
            ["filepath", "old_string", "new_string"],
        ),
        schema(
            "write_file",
            "Create or overwrite a file, creating parent dirs.",
            {"filepath": {"type": "string"}, "content": {"type": "string"}},
            ["filepath", "content"],
        ),
        schema(
            "run_command",
            "Run a shell command in /workspace via /bin/bash -c.",
            {"command": {"type": "string"}},
            ["command"],
        ),
        schema("submit_patch", "Stage untracked intents and capture git diff HEAD.", {}, []),
        schema("get_status", "Real-time budget consumption and patch status.", {}, []),
        schema(
            "get_code_neighbors",
            "In/out neighbors of a symbol in the repo call/dependency graph.",
            {
                "node": {"type": "string"},
                "edge_type": {"type": "string"},
                "max_neighbors": {"type": "integer"},
            },
            ["node"],
        ),
        schema(
            "search_similar_code",
            "Top-k graph nodes by embedding cosine similarity to the query.",
            {"query": {"type": "string"}, "k": {"type": "integer"}},
            ["query"],
        ),
        schema(
            "get_code_subgraph",
            "Induced subgraph (nodes + interconnecting edges) for symbols.",
            {"nodes": {"type": "array", "items": {"type": "string"}}},
            ["nodes"],
        ),
    ]


class ToolExecutor:
    def __init__(
        self,
        workspace: str | Path,
        graph: GraphStore,
        budget: Budget,
        llm=None,
        command_timeout: int = 300,
    ):
        self.workspace = Path(workspace).resolve()
        self.graph = graph
        self.budget = budget
        self.llm = llm
        self.command_timeout = command_timeout

    def _resolve(self, filepath: str) -> Path:
        candidate = (self.workspace / filepath).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            raise ValueError(f"fora do /workspace: {filepath}")
        return candidate

    def _run_git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return (proc.stdout + proc.stderr)[-OUTPUT_CAP:]

    def call(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"unknown tool: {name}"
        try:
            return handler(args or {})
        except Exception as exc:  # ferramenta nunca quebra o loop; devolve o erro
            return f"error: {type(exc).__name__}: {exc}"

    def _tool_read_file(self, args: dict) -> str:
        path = self._resolve(str(args.get("filepath", "")))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = int(args.get("start_line") or 1) - 1
        end = args.get("end_line")
        end = int(end) if end else len(lines)
        return "\n".join(lines[max(0, start) : max(0, end)])[:OUTPUT_CAP]

    def _tool_edit_file(self, args: dict) -> str:
        path = self._resolve(str(args.get("filepath", "")))
        old, new = str(args.get("old_string", "")), str(args.get("new_string", ""))
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text:
            return "error: empty file"
        count = text.count(old)
        if count == 0:
            return "error: old_string not found"
        if count > 1 and not args.get("allow_multiple"):
            return f"error: old_string matches {count}x (set allow_multiple)"
        path.write_text(text.replace(old, new), encoding="utf-8")
        return f"edited {path.name}: {count} replacement(s)"

    def _tool_write_file(self, args: dict) -> str:
        path = self._resolve(str(args.get("filepath", "")))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args.get("content", "")), encoding="utf-8")
        return f"wrote {path.name} ({len(args.get('content', ''))} chars)"

    def _tool_run_command(self, args: dict) -> str:
        try:
            proc = subprocess.run(
                str(args.get("command", "")),
                shell=True,
                executable="/bin/bash",
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
            )
        except subprocess.TimeoutExpired:
            return f"error: timeout after {self.command_timeout}s"
        output = (proc.stdout + proc.stderr)[-OUTPUT_CAP:]
        return f"exit={proc.returncode}\n{output}"

    def _tool_submit_patch(self, args: dict) -> str:
        _ = args
        self._run_git("add", "-N", ".")
        diff = self._run_git("diff", "HEAD")
        self.budget.patches += 1
        if not diff.strip():
            return "empty diff (no changes vs HEAD)"
        return diff[:OUTPUT_CAP]

    def _tool_get_status(self, args: dict) -> str:
        _ = args
        status = self.budget.status(self.llm)
        return " ".join(f"{key}={value}" for key, value in status.items())

    def _tool_get_code_neighbors(self, args: dict) -> str:
        result = self.graph.neighbors(
            str(args.get("node", "")),
            args.get("edge_type"),
            int(args.get("max_neighbors") or 50),
        )
        ins = ", ".join(result["in"][:20]) or "-"
        outs = ", ".join(result["out"][:20]) or "-"
        return f"{result['node']}\n in ({len(result['in'])}): {ins}\n out ({len(result['out'])}): {outs}"

    def _tool_search_similar_code(self, args: dict) -> str:
        result = self.graph.search_similar(str(args.get("query", "")), int(args.get("k") or 10))
        hits = "\n".join(f"- {node}" for node in result["hits"]) or "-"
        return f"{hits}\n[{result['note']}]"

    def _tool_get_code_subgraph(self, args: dict) -> str:
        nodes = [str(node) for node in args.get("nodes") or []]
        result = self.graph.subgraph(nodes)
        edges = "\n".join(f"{src} -> {dst}" for src, dst in result["edges"][:60])
        return f"nodes: {len(result['nodes'])}\nedges:\n{edges or '-'}"
