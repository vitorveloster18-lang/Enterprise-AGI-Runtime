"""CLI runtime resolution."""

from __future__ import annotations

from pathlib import Path

import typer

from ..core.errors import EGRError, WorkspaceNotFound
from ..runtime.runtime import Runtime
from .formatting import error


def get_runtime(workspace: Path | None = None, environment: str | None = None) -> Runtime:
    try:
        return Runtime.load(workspace, environment=environment)
    except WorkspaceNotFound as exc:
        error(str(exc))
        raise typer.Exit(code=2) from exc
    except EGRError as exc:
        error(f"{type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc
