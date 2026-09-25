"""Tasks + sandbox git (baseline limpa para o diff capturar só o patch)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from pathlib import Path


def load_tasks(path: str | Path) -> list[dict]:
    tasks = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def task_id(task: dict) -> str:
    for key in ("task_id", "id", "instance_id", "name"):
        if task.get(key):
            return str(task[key])
    return "task-?"


def problem_text(task: dict) -> str:
    for key in ("problem", "problem_statement", "issue", "description", "prompt"):
        if task.get(key):
            return str(task[key])
    return json.dumps(task)[:2000]


def test_command(task: dict) -> str:
    for key in ("test_command", "validation", "tests"):
        if task.get(key):
            value = task[key]
            if isinstance(value, list):
                return " && ".join(str(item) for item in value)
            return str(value)
    return ""


def prepare(task: dict, work_dir: str | Path, data_dir: str | Path) -> Path:
    """Materializa o repo da task num diretório com commit baseline limpo."""
    workspace = Path(work_dir) / task_id(task)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    data = Path(data_dir)
    placed = False
    snapshot = task.get("snapshot") or task.get("repo_snapshot") or task.get("repo")
    if snapshot:
        candidate = Path(str(snapshot))
        if not candidate.is_absolute():
            candidate = data / candidate
        if candidate.is_file() and candidate.suffix in (".tgz", ".gz"):
            with tarfile.open(candidate) as tar:
                tar.extractall(workspace)
            placed = True
        elif candidate.is_dir():
            shutil.copytree(candidate, workspace, dirs_exist_ok=True)
            placed = True
    repo_url = task.get("repo_url") or task.get("url")
    if not placed and repo_url:
        subprocess.run(
            ["git", "clone", "--depth", "1", str(repo_url), str(workspace)],
            capture_output=True,
            timeout=300,
            check=True,
        )
        placed = True
    if not placed:
        raise SystemExit(
            f"task {task_id(task)}: sem repo (chaves vistas: {sorted(task)}). "
            "Rode inspect.py, mande a saída e o adaptador é ajustado."
        )
    base = task.get("base_commit") or task.get("commit")
    if base:
        subprocess.run(
            ["git", "checkout", str(base)], cwd=workspace, capture_output=True, timeout=120
        )
    subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True, timeout=120)
    subprocess.run(
        ["git", "-c", "user.email=localrun", "-c", "user.name=localrun",
         "commit", "-m", "baseline", "--allow-empty"],
        cwd=workspace,
        capture_output=True,
        timeout=120,
    )
    setup = task.get("setup_command") or task.get("setup")
    if setup:
        subprocess.run(
            str(setup), shell=True, executable="/bin/bash", cwd=workspace,
            capture_output=True, timeout=600,
        )
    return workspace
