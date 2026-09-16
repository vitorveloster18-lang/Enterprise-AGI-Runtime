"""Workspace discovery and path confinement."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import SandboxViolation, WorkspaceNotFound

CONFIG_FILE = "egr.yaml"
MARKERS = (CONFIG_FILE, ".egr")


def find_workspace_root(start: Path | str | None = None) -> Path | None:
    """Walk up from `start` looking for an EGR workspace (egr.yaml or .egr/)."""
    current = Path(start or os.getcwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_FILE).exists() or (candidate / ".egr").is_dir():
            return candidate
    return None


def require_workspace_root(start: Path | str | None = None) -> Path:
    root = os.environ.get("EGR_WORKSPACE")
    if root:
        return Path(root).resolve()
    found = find_workspace_root(start)
    if found is None:
        raise WorkspaceNotFound(
            f"no EGR workspace found (looked for {CONFIG_FILE}). Run `egr init` first."
        )
    return found


def ensure_inside(root: Path, target: Path) -> Path:
    """Resolve `target` and guarantee it stays inside `root`."""
    root_resolved = root.resolve()
    target_resolved = (root_resolved / target).resolve() if not target.is_absolute() else target.resolve()
    if root_resolved == target_resolved or root_resolved in target_resolved.parents:
        return target_resolved
    raise SandboxViolation(f"path '{target}' escapes the allowed root '{root_resolved}'")


def resolve_within(root: Path, target: str | Path) -> Path:
    """Resolve a user-supplied path inside `root`, refusing escapes."""
    return ensure_inside(root, Path(target))


__all__ = [
    "CONFIG_FILE",
    "SandboxViolation",
    "WorkspaceNotFound",
    "ensure_inside",
    "find_workspace_root",
    "require_workspace_root",
    "resolve_within",
]
