-- Fase 6: execução de workflow como objeto auditado.
--
-- O workflow em si continua sendo YAML versionado; o que vive no banco é o
-- **run**: quem disparou, quando, o que cada passo produziu e por que parou.
-- Os passos ficam no JSON do run (transação única, sem risco de divergência).

CREATE TABLE IF NOT EXISTS workflow_runs (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    status      TEXT NOT NULL,
    environment TEXT,
    trigger     TEXT,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_workflow  ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_runs_status    ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_trigger   ON workflow_runs(trigger);
CREATE INDEX IF NOT EXISTS idx_runs_created   ON workflow_runs(created_at DESC);
