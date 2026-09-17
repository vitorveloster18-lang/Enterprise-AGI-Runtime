"""Sandbox de prova — executa uma ferramenta proposta sem tocar no workspace.

O julgamento de uma proposta de código não é "eu li e parece bom": é rodar.
Este módulo executa a ferramenta candidata em um diretório descartável, com

  • interpretador novo (nada do estado do Runtime é herdado),
  • ambiente filtrado (sem segredos — ver SandboxRunner._process_env),
  • `dry_run=True` (ferramentas bem-comportadas não têm efeito colateral),
  • teto de tempo e coleta do que foi escrito,
  • contêiner quando houver runtime (o sandbox da Fase 3, ADR-013).

A prova é necessária mas não suficiente: `dry_run` é uma convenção que o autor
da ferramenta pode ignorar. Por isso nenhuma ferramenta entra sem aprovação
humana — a prova reduz a assimetria, não substitui o governo.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..core.config import SandboxConfig
from ..domain.proposal import TrialReport
from ..tools.sandbox import SandboxRunner

MARKER = "__EGR_TRIAL__"
DEFAULT_TIMEOUT = 10
MAX_OUTPUT_CHARS = 4000


def module_filename(tool_name: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", (tool_name or "tool").lower()).strip("_")
    return f"{safe or 'tool'}.py"


def build_trial_script(module: str, tool_name: str, args: dict, timeout: int, agent_id: str | None = None) -> str:
    """Script que importa o módulo proposto e executa a ferramenta uma vez."""

    payload = json.dumps(args or {}, ensure_ascii=False)
    return f'''# gerado pelo EGR — executa uma ferramenta candidata dentro do sandbox
import json, sys, traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent
ARGS = json.loads({payload!r})
TOOL = {tool_name!r}

try:
    from egr.domain.enums import Environment
    from egr.domain.tool import ToolRequest
    from egr.tools.protocol import Tool, ToolContext

    import importlib.util
    spec = importlib.util.spec_from_file_location("egr_trial_module", BASE / {module!r})
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    candidates = [
        obj for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, Tool) and obj is not Tool
    ]
    chosen = None
    for cls in candidates:
        declared = getattr(getattr(cls, "spec", None), "name", None)
        if declared == TOOL:
            chosen = cls
            break
    if chosen is None and candidates:
        chosen = candidates[0]
    if chosen is None:
        raise RuntimeError("nenhuma classe Tool encontrada no módulo")

    ctx = ToolContext(
        workspace=BASE / "workspace",
        sandbox=BASE / "sandbox",
        artifacts=BASE / "artifacts",
        environment=Environment.DEVELOPMENT,
        dry_run=True,
        timeout={int(timeout)},
        agent_id={agent_id!r},
        task_id="trial",
    )
    ctx.workspace.mkdir(parents=True, exist_ok=True)
    ctx.sandbox.mkdir(parents=True, exist_ok=True)
    ctx.artifacts.mkdir(parents=True, exist_ok=True)

    request = ToolRequest(tool=TOOL, args=ARGS, agent_id={agent_id!r}, environment=Environment.DEVELOPMENT)
    result = chosen().execute(request, ctx)
    print({MARKER!r} + json.dumps({{
        "ok": bool(getattr(result, "ok", False)),
        "output": getattr(result, "output", None),
        "error": getattr(result, "error", None),
        "tool": getattr(getattr(chosen, "spec", None), "name", TOOL),
        "cost": float(getattr(result, "cost", 0.0) or 0.0),
        "artifacts": list(getattr(result, "artifacts", []) or [])[:5],
    }}, ensure_ascii=False, default=str)[:{MAX_OUTPUT_CHARS}])
except BaseException:  # noqa: BLE001 - o relatório precisa do erro, seja ele qual for
    print({MARKER!r} + json.dumps({{
        "ok": False,
        "error": traceback.format_exc(limit=6)[-2000:],
    }}, ensure_ascii=False, default=str)[:{MAX_OUTPUT_CHARS}])
'''


def run_trial(
    *,
    content: str,
    tool_name: str,
    args: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    agent_id: str | None = None,
    container: bool = False,
    keep_dir: Path | None = None,
) -> TrialReport:
    """Executa a ferramenta candidata em um diretório descartável."""

    root = Path(keep_dir) if keep_dir else Path(tempfile.mkdtemp(prefix="egr-trial-"))
    sandbox_dir = root / "sandbox"
    workspace_dir = root / "workspace"
    artifacts_dir = root / "artifacts"
    for directory in (sandbox_dir, workspace_dir, artifacts_dir):
        directory.mkdir(parents=True, exist_ok=True)

    module = module_filename(tool_name)
    (sandbox_dir / module).write_text(content, encoding="utf-8")
    script = build_trial_script(module, tool_name, args or {}, timeout, agent_id=agent_id)

    config = SandboxConfig(mode="container" if container else "process", timeout=timeout)
    runner = SandboxRunner(
        config,
        workspace=workspace_dir,
        sandbox_dir=sandbox_dir,
        artifacts_dir=artifacts_dir,
        environment="development",
        agent_id=agent_id,
    )

    try:
        result = runner.run(script, timeout=timeout)
        payload = _parse(result.stdout)
        files = _created_files(root, skip={module})
        return TrialReport(
            ok=bool(payload.get("ok")) and result.exit_code == 0 and not result.error,
            mode=result.mode,
            duration_ms=result.duration_ms,
            exit_code=result.exit_code,
            output=payload.get("output"),
            error=payload.get("error") or result.error or (result.stderr[-2000:] if not payload else None),
            cost=float(payload.get("cost") or 0.0),
            stdout=result.stdout[-2000:],
            stderr=result.stderr[-2000:],
            files=files,
            args=args or {},
            timed_out=bool(result.error and "timed out" in result.error),
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _parse(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith(MARKER):
            try:
                return dict(json.loads(line[len(MARKER) :]))
            except json.JSONDecodeError:
                return {"ok": False, "error": "saída do trial não é JSON válido"}
    return {}


def _created_files(root: Path, skip: set[str]) -> list[str]:
    created: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in skip or "__pycache__" in path.parts:
            continue
        created.append(str(path.relative_to(root)))
    return created[:50]


__all__ = ["DEFAULT_TIMEOUT", "build_trial_script", "module_filename", "run_trial"]
