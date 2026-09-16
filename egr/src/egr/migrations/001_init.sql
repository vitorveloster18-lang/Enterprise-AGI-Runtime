-- EGR V1 - initial schema (SQLite backend for the local / on-premise runtime).
-- Postgres backend arrives with the Enterprise Platform phase; the repository
-- layer is the only thing that changes.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enterprises (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    version     TEXT NOT NULL DEFAULT '1.0.0',
    environment TEXT NOT NULL DEFAULT 'development',
    data        TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    environment TEXT NOT NULL,
    agent_id    TEXT,
    objective   TEXT,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agent   ON tasks(agent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS policies (
    id       TEXT PRIMARY KEY,
    priority INTEGER NOT NULL DEFAULT 0,
    enabled  INTEGER NOT NULL DEFAULT 1,
    data     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    task_id     TEXT,
    environment TEXT,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

-- Append-only audit ledger (hash-chained).
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL,
    actor       TEXT,
    task_id     TEXT,
    agent_id    TEXT,
    environment TEXT,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    prev_hash   TEXT,
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_task      ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_type      ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_created   ON events(created_at DESC);

CREATE TABLE IF NOT EXISTS artifacts (
    id          TEXT PRIMARY KEY,
    kind        TEXT,
    task_id     TEXT,
    environment TEXT,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);

CREATE TABLE IF NOT EXISTS memory_records (
    id         TEXT PRIMARY KEY,
    namespace  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_namespace ON memory_records(namespace);
CREATE INDEX IF NOT EXISTS idx_memory_kind      ON memory_records(kind);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    id UNINDEXED,
    namespace UNINDEXED,
    content,
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
