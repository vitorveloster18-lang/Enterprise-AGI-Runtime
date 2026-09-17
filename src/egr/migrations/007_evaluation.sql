-- Fase 8: Evaluation — provar qualidade, custo, latência e segurança.
--
-- A suíte é YAML versionado; o que vive no banco é a **execução** medida:
-- casos, métricas, comparação contra a baseline e achados de segurança.

CREATE TABLE IF NOT EXISTS evaluation_suites (
    id          TEXT PRIMARY KEY,
    target_kind TEXT NOT NULL,          -- tool | workflow | agent | policy
    target      TEXT NOT NULL,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suites_target ON evaluation_suites(target_kind, target);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id          TEXT PRIMARY KEY,
    suite_id    TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target      TEXT NOT NULL,
    status      TEXT NOT NULL,          -- passed | failed | regressed | error
    environment TEXT,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_suite   ON evaluation_runs(suite_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_target  ON evaluation_runs(target_kind, target);
CREATE INDEX IF NOT EXISTS idx_eval_runs_status  ON evaluation_runs(status);
CREATE INDEX IF NOT EXISTS idx_eval_runs_created ON evaluation_runs(created_at DESC);
