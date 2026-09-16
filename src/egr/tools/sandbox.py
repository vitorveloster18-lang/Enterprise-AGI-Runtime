"""Sandbox Runner — isolamento real da execução de código (Fase 3).

Dois backends:

  container  docker/podman: rede desligada, memória/CPU/pids limitados,
             workspace montado read-only, sandbox read-write, tmpfs em /tmp
  process    subprocesso local com ambiente filtrado (isolamento fraco:
             depende de política e confinamento de paths — ver ADR-013)

`mode: auto` usa contêiner quando há runtime disponível e cai para processo
com um aviso registrado no ledger.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import SandboxConfig
from ..core.logging import get_logger

LOGGER = get_logger("egr.sandbox")

SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")


@dataclass
class SandboxResult:
    ok: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    mode: str = "process"
    image: str | None = None
    network: bool = False
    artifacts: list[dict] = field(default_factory=list)
    error: str | None = None
    metadata: dict = field(default_factory=dict)


class SandboxRunner:
    def __init__(
        self,
        config: SandboxConfig,
        workspace: Path,
        sandbox_dir: Path,
        artifacts_dir: Path,
        environment: str = "development",
        task_id: str | None = None,
        agent_id: str | None = None,
    ):
        self.config = config
        self.workspace = Path(workspace).resolve()
        self.sandbox_dir = Path(sandbox_dir).resolve()
        self.artifacts_dir = Path(artifacts_dir).resolve()
        self.environment = environment
        self.task_id = task_id
        self.agent_id = agent_id

    # ---- backend selection -------------------------------------------
    def runtime_available(self) -> str | None:
        """Return the container runtime binary if it is installed and usable."""

        if self.config.mode == "process":
            return None
        binary = shutil.which(self.config.runtime)
        if not binary:
            return None
        try:
            completed = subprocess.run(
                [binary, "info"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return binary if completed.returncode == 0 else None

    def resolved_mode(self) -> str:
        if self.config.mode == "container":
            return "container"
        if self.config.mode == "process":
            return "process"
        return "container" if self.runtime_available() else "process"

    # ---- execution ----------------------------------------------------
    def run(self, script: str, timeout: int | None = None) -> SandboxResult:
        timeout = timeout or self.config.timeout
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        mode = self.resolved_mode()
        start = time.perf_counter()
        before = self._snapshot()

        result = (
            self._run_container(script, timeout)
            if mode == "container"
            else self._run_process(script, timeout)
        )

        result.duration_ms = int((time.perf_counter() - start) * 1000)
        result.mode = mode
        result.network = self.config.network if mode == "container" else True
        result.image = self.config.image if mode == "container" else None
        result.artifacts = self._collect_artifacts(before)
        result.metadata.update(
            {
                "task_id": self.task_id,
                "agent_id": self.agent_id,
                "requested_mode": self.config.mode,
                "workspace_read_only": self.config.read_only_workspace if mode == "container" else False,
            }
        )
        return result

    # ---- backends -----------------------------------------------------
    def _run_container(self, script: str, timeout: int) -> SandboxResult:
        binary = self.runtime_available()
        if not binary:
            return SandboxResult(
                ok=False,
                exit_code=-1,
                error=(
                    "modo 'container' exigido mas nenhum runtime disponível "
                    f"('{self.config.runtime}'). Instale o Docker/Podman ou use mode: process"
                ),
                mode="container",
            )

        command = [
            binary,
            "run",
            "--rm",
            "-i",
            "--network",
            "none" if not self.config.network else "bridge",
            "--memory",
            self.config.memory,
            "--cpus",
            self.config.cpus,
            "--pids-limit",
            str(self.config.pids_limit),
            "--tmpfs",
            f"/tmp:rw,size={self.config.tmpfs_size}",
            "-v",
            f"{self.workspace}:/workspace:{'ro' if self.config.read_only_workspace else 'rw'}",
            "-v",
            f"{self.sandbox_dir}:/sandbox:rw",
            "-w",
            "/sandbox",
        ]
        for key, value in self._container_env().items():
            command += ["-e", f"{key}={value}"]
        command += [self.config.image, "python", "-"]

        try:
            completed = subprocess.run(
                command,
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(ok=False, exit_code=-1, error=f"script timed out after {timeout}s")
        except OSError as exc:
            return SandboxResult(ok=False, exit_code=-1, error=f"sandbox failed: {exc}")

        return SandboxResult(
            ok=completed.returncode == 0,
            exit_code=completed.returncode,
            stdout=completed.stdout[-8000:],
            stderr=completed.stderr[-4000:],
        )

    def _run_process(self, script: str, timeout: int) -> SandboxResult:
        script_path = self.sandbox_dir / f"_egr_script_{uuid.uuid4().hex[:8]}.py"
        script_path.write_text(script, encoding="utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(self.sandbox_dir),
                env=self._process_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(ok=False, exit_code=-1, error=f"script timed out after {timeout}s")
        finally:
            script_path.unlink(missing_ok=True)

        return SandboxResult(
            ok=completed.returncode == 0,
            exit_code=completed.returncode,
            stdout=completed.stdout[-8000:],
            stderr=completed.stderr[-4000:],
        )

    # ---- helpers ------------------------------------------------------
    def _container_env(self) -> dict[str, str]:
        return {
            "EGR_WORKSPACE": "/workspace",
            "EGR_SANDBOX": "/sandbox",
            "EGR_ARTIFACTS": "/sandbox",
            "EGR_ENVIRONMENT": self.environment,
            "EGR_TASK_ID": self.task_id or "",
            "EGR_AGENT_ID": self.agent_id or "",
            "EGR_SANDBOX_MODE": "container",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "",
        }

    def _process_env(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS)
        }
        env.update(
            {
                "HOME": str(self.sandbox_dir),
                "PYTHONPATH": "",
                "PYTHONDONTWRITEBYTECODE": "1",
                "EGR_WORKSPACE": str(self.workspace),
                "EGR_SANDBOX": str(self.sandbox_dir),
                "EGR_ARTIFACTS": str(self.artifacts_dir),
                "EGR_ENVIRONMENT": self.environment,
                "EGR_TASK_ID": self.task_id or "",
                "EGR_AGENT_ID": self.agent_id or "",
                "EGR_SANDBOX_MODE": "process",
            }
        )
        return env

    def _snapshot(self) -> dict[Path, float]:
        return {path: path.stat().st_mtime for path in self.sandbox_dir.rglob("*") if path.is_file()}

    def _collect_artifacts(self, before: dict[Path, float]) -> list[dict]:
        destination_dir = self.artifacts_dir / (self.task_id or "adhoc")
        produced: list[dict] = []
        for path in sorted(self.sandbox_dir.rglob("*")):
            if not path.is_file() or path.name.startswith("_egr_script_"):
                continue
            if path in before and path.stat().st_mtime <= before[path]:
                continue
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / path.name
            destination.write_bytes(path.read_bytes())
            produced.append(
                {
                    "name": path.name,
                    "path": str(self._relative(destination)),
                    "absolute": str(destination),
                    "bytes": destination.stat().st_size,
                    "content_type": (
                        "text/markdown" if destination.suffix in {".md", ".txt"} else "application/octet-stream"
                    ),
                }
            )
        return produced

    def _relative(self, path: Path) -> Path:
        try:
            return path.relative_to(self.workspace)
        except ValueError:
            return path

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.resolved_mode(),
            "configured": self.config.mode,
            "runtime": self.config.runtime,
            "image": self.config.image,
            "network": self.config.network,
            "memory": self.config.memory,
            "cpus": self.config.cpus,
            "pids_limit": self.config.pids_limit,
            "runtime_available": bool(self.runtime_available()),
        }
