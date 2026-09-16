"""Versioned SQL migrations, applied in order and recorded in schema_migrations."""

from __future__ import annotations

from importlib.resources import files

from ..core.timeutil import iso
from .database import Database

MIGRATIONS_PACKAGE = "egr.migrations"


def available_migrations() -> list[tuple[str, str]]:
    migrations: list[tuple[str, str]] = []
    for resource in files(MIGRATIONS_PACKAGE).iterdir():
        name = resource.name
        if not name.endswith(".sql"):
            continue
        version = name.split("_", 1)[0]
        migrations.append((version, resource.read_text(encoding="utf-8")))
    return sorted(migrations, key=lambda item: item[0])


def applied_versions(db: Database) -> set[str]:
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    return {row["version"] for row in db.query("SELECT version FROM schema_migrations")}


def apply_migrations(db: Database) -> list[str]:
    done = applied_versions(db)
    applied: list[str] = []
    for version, script in available_migrations():
        if version in done:
            continue
        with db.transaction():
            db.executescript(script)
            db.execute(
                "INSERT OR REPLACE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, iso()),
            )
        applied.append(version)
    db.commit()
    return applied


def migration_status(db: Database) -> dict:
    available = available_migrations()
    done = applied_versions(db)
    return {
        "applied": [version for version, _ in available if version in done],
        "pending": [version for version, _ in available if version not in done],
        "total": len(available),
    }
