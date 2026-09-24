"""Logging setup.

Two sinks:
  * stderr / console  -> for humans using the CLI (rich)
  * logs/egr.jsonl    -> structured log for observability (Phase: Infrastructure)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

console = Console()
error_console = Console(stderr=True)

_FORMAT = "%(message)s"


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("egr")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.propagate = False

    handler = RichHandler(console=error_console, rich_tracebacks=False, show_path=False)
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(_JSONFormatter())
        logger.addHandler(file_handler)

    return logger


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = "egr") -> logging.Logger:
    return logging.getLogger(name)


class _StdoutCapture:
    def write(self, message):  # pragma: no cover - convenience only
        console.print(message, end="")

    def flush(self):  # pragma: no cover
        sys.stdout.flush()
