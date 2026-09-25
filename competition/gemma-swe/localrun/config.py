"""Config do mini-harness: tudo por env, com tetos anti-surpresa na fatura."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    base_url: str
    tasks_file: str
    data_dir: str
    work_dir: str
    out_dir: str
    max_tasks: int
    max_turns: int
    task_timeout: int
    max_tokens: int
    temperature: float


def load() -> Settings:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY ausente (export GEMINI_API_KEY=...)")
    return Settings(
        api_key=api_key,
        model=os.environ.get("GEMMA_MODEL", "gemma-4-31b-it"),
        base_url=os.environ.get(
            "GEMMA_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
        ),
        tasks_file=os.environ.get("TASKS_JSONL", "tasks.jsonl"),
        data_dir=os.environ.get("DATA_DIR", "data"),
        work_dir=os.environ.get("WORK_DIR", "work"),
        out_dir=os.environ.get("OUT_DIR", "out"),
        max_tasks=int(os.environ.get("MAX_TASKS", "3")),
        max_turns=int(os.environ.get("MAX_TURNS", "40")),
        task_timeout=int(os.environ.get("TASK_TIMEOUT", "1500")),
        max_tokens=int(os.environ.get("MAX_TOKENS", "200000")),
        temperature=float(os.environ.get("TEMPERATURE", "0.0")),
    )
