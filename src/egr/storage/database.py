"""SQLite connection wrapper.

SQLite is the V1 local/on-premise backend: zero infra, offline capable, and it
keeps the runtime truly local. The repository layer is the only boundary that a
future PostgreSQL backend has to satisfy.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Database:
    """Thread-safe SQLite wrapper.

    The API server serves requests in a worker thread, so the connection is
    shared (`check_same_thread=False`) and guarded by a re-entrant lock.
    """

    def __init__(self, path: Path | str, *, wal: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(str(self.path), timeout=30.0, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        with self._lock:
            self.connection.execute("PRAGMA foreign_keys = ON")
            if wal:
                self.connection.execute("PRAGMA journal_mode = WAL")
                self.connection.execute("PRAGMA synchronous = NORMAL")

    # ---- low level ---------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.connection.execute(sql, params)

    def executescript(self, sql: str) -> sqlite3.Cursor:
        with self._lock:
            return self.connection.executescript(sql)

    def executemany(self, sql: str, params: list[tuple]) -> sqlite3.Cursor:
        with self._lock:
            return self.connection.executemany(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.connection.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.query_one(sql, params)
        return row[0] if row else None

    def commit(self) -> None:
        with self._lock:
            self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            try:
                yield
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    def close(self) -> None:
        self.connection.close()
