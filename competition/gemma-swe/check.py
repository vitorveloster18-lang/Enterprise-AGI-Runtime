#!/usr/bin/env python3
"""Valida o esqueleto da submissão (só stdlib + pyyaml do EGR).

Confere: YAML parseável, chaves obrigatórias, !includes existentes e sem
fuga de diretório (../), sub-agentes referenciados, tools conhecidas e
SKILL.md com frontmatter `name:`. Não valida contra o HARNESS oficial —
isso é o checklist do README.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
MODEL = "gemma-4-31b-it-qat-w4a16-ct"
KNOWN_TOOLS = {
    "run_command",
    "submit_patch",
    "get_status",
    "read_file",
    "edit_file",
    "write_file",
    "get_code_neighbors",
    "search_similar_code",
    "get_code_subgraph",
    "run_skill_script",
    "load_skill_resource",
}

errors: list[str] = []
warnings: list[str] = []


def load_yaml(path: Path):
    """Parse tolerando !include (devolve o alvo como string marcada)."""

    class IncludeLoader(yaml.SafeLoader):
        pass

    def _include(loader, node):
        return f"!include {loader.construct_scalar(node)}"

    IncludeLoader.add_constructor("!include", _include)
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.load(fh, Loader=IncludeLoader), []
    except yaml.YAMLError as exc:
        return None, [f"{path.name}: YAML inválido: {exc}"]


def resolve_include(value: str, base: Path) -> Path | None:
    target = value.replace("!include", "", 1).strip()
    if target.startswith("/"):
        return None
    candidate = (base / target).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return candidate


def check_agent(path: Path, is_root: bool = False):
    data, problems = load_yaml(path)
    errors.extend(problems)
    if data is None:
        return
    label = path.relative_to(ROOT)
    for key in ("name", "instruction"):
        if key not in data:
            errors.append(f"{label}: falta '{key}'")
    if (data.get("model") or MODEL) != MODEL:
        warnings.append(f"{label}: model != {MODEL}")
    instruction = data.get("instruction", "")
    if isinstance(instruction, str) and instruction.startswith("!include"):
        target = resolve_include(instruction, path.parent)
        if target is None or not target.exists():
            errors.append(f"{label}: include inválido/inexistente: {instruction}")
    sampling = data.get("generate_content_config")
    if isinstance(sampling, str) and sampling.startswith("!include"):
        target = resolve_include(sampling, path.parent)
        if target is None or not target.exists():
            errors.append(f"{label}: sampling inválido/inexistente: {sampling}")
    for tool in data.get("tools") or []:
        name = tool.get("name") if isinstance(tool, dict) else None
        if name not in KNOWN_TOOLS:
            warnings.append(f"{label}: tool desconhecida (confirmar no HARNESS): {name}")
    for sub in data.get("sub_agents") or []:
        ref = sub.get("config_path") if isinstance(sub, dict) else None
        if not ref:
            errors.append(f"{label}: sub_agent sem config_path")
            continue
        target = (path.parent / ref).resolve()
        if not target.exists():
            errors.append(f"{label}: sub-agente inexistente: {ref}")
        elif is_root:
            check_agent(target)


def check_skills():
    skills = ROOT / "skills"
    if not skills.is_dir():
        warnings.append("sem diretório skills/")
        return
    for skill in sorted(skills.iterdir()):
        if not skill.is_dir():
            continue
        manifest = skill / "SKILL.md"
        if not manifest.exists():
            errors.append(f"skills/{skill.name}: falta SKILL.md")
            continue
        text = manifest.read_text(encoding="utf-8")
        if not re.match(r"^---\nname:\s*\S+", text):
            errors.append(f"skills/{skill.name}: SKILL.md sem frontmatter 'name:'")


def main() -> int:
    root = ROOT / "agent.yaml"
    if not root.exists():
        print("ERRO: agent.yaml ausente na raiz")
        return 1
    check_agent(root, is_root=True)
    if not (ROOT / "eval_config.yaml").exists():
        warnings.append("sem eval_config.yaml (opcional)")
    check_skills()
    for warning in warnings:
        print(f"AVISO: {warning}")
    for error in errors:
        print(f"ERRO: {error}")
    if errors:
        print(f"\n{len(errors)} erro(s), {len(warnings)} aviso(s)")
        return 1
    print(f"OK: esqueleto válido ({len(warnings)} aviso(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
