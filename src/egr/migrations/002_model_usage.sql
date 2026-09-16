-- Fase 2: telemetria de modelos (custo, latência, tokens) por task/agente.

CREATE TABLE IF NOT EXISTS model_calls (
    id            TEXT PRIMARY KEY,
    task_id       TEXT,
    agent_id      TEXT,
    provider      TEXT NOT NULL,
    model         TEXT,
    capability    TEXT,
    external      INTEGER NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost          REAL NOT NULL DEFAULT 0.0,
    status        TEXT NOT NULL DEFAULT 'ok',
    error         TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_calls_task      ON model_calls(task_id);
CREATE INDEX IF NOT EXISTS idx_model_calls_provider  ON model_calls(provider);
CREATE INDEX IF NOT EXISTS idx_model_calls_created   ON model_calls(created_at DESC);
