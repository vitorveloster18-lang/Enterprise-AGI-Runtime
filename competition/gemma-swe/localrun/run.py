#!/usr/bin/env python3
"""CLI do mini-harness: `python -m localrun.run --tasks 3` (a partir desta pasta)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "competition" / "gemma-swe"))

from localrun.agent import run_task  # noqa: E402
from localrun.config import load  # noqa: E402
from localrun.graph import GraphStore  # noqa: E402
from localrun.llm import LLMClient  # noqa: E402
from localrun.tasks import load_tasks, prepare, problem_text, task_id, test_command  # noqa: E402
from localrun.tools import Budget, ToolExecutor  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Mini-harness Gemma SWE (via API)")
    parser.add_argument("--tasks", type=int, default=None, help="quantas tasks (padrão: MAX_TASKS)")
    parser.add_argument("--only", default=None, help="só a task com este id")
    parser.add_argument("--list", action="store_true", help="lista ids e sai")
    parser.add_argument("--dry-run", action="store_true", help="só prepara sandboxes, sem LLM")
    args = parser.parse_args()

    settings = load()
    tasks = load_tasks(settings.tasks_file)
    if args.list:
        for task in tasks:
            print(task_id(task))
        return 0
    if args.only:
        tasks = [task for task in tasks if task_id(task) == args.only]
    limit = args.tasks or settings.max_tasks
    tasks = tasks[:limit]
    print(f"tasks: {len(tasks)} | model: {settings.model} | teto: {settings.max_tokens} tokens")

    out = Path(settings.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    llm = LLMClient(settings.base_url, settings.api_key, settings.model)
    graph = GraphStore(settings.data_dir)
    results = []
    for task in tasks:
        tid = task_id(task)
        print(f"\n=== {tid} ===")
        workspace = prepare(task, settings.work_dir, settings.data_dir)
        print(f"sandbox: {workspace}")
        if args.dry_run:
            continue
        budget = Budget(task_id=tid)
        tools = ToolExecutor(workspace, graph, budget, llm)
        started = time.time()
        try:
            summary = run_task(
                llm, tools, budget, problem_text(task),
                settings.max_turns, settings.max_tokens, settings.temperature,
            )
        except Exception as exc:  # task nunca derruba a rodada
            summary = {"error": f"{type(exc).__name__}: {exc}"}
        summary["seconds"] = round(time.time() - started, 1)
        command = test_command(task)
        if command:
            try:
                proc = subprocess.run(
                    command, shell=True, executable="/bin/bash", cwd=workspace,
                    capture_output=True, text=True, timeout=settings.task_timeout,
                )
                summary["passed"] = proc.returncode == 0
                summary["test_tail"] = (proc.stdout + proc.stderr)[-1500:]
            except subprocess.TimeoutExpired:
                summary["passed"] = False
                summary["test_tail"] = "timeout"
        else:
            summary["passed"] = None
        diff = subprocess.run(
            ["git", "diff", "HEAD"], cwd=workspace, capture_output=True, text=True, timeout=60
        ).stdout
        (out / f"{tid}.patch").write_text(diff, encoding="utf-8")
        summary["id"] = tid
        results.append(summary)
        print(json.dumps({k: summary.get(k) for k in ("passed", "rounds", "verdict", "turns", "tokens", "seconds", "error")}, ensure_ascii=False))
        if llm.total_tokens >= settings.max_tokens:
            print("teto de tokens atingido; parando")
            break
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    scored = [r for r in results if r.get("passed") is not None]
    passed = sum(1 for r in scored if r.get("passed"))
    print(f"\nplacar: {passed}/{len(scored)} | tokens: {llm.total_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
