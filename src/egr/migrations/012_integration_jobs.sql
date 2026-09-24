-- Fase 12 (2/2): fila de saída para chamadas a sistemas externos.
--
-- Sistema alheio falha; o Runtime não esquece. O job fica pendente, tenta de novo
-- com espera crescente e, se esgotar as tentativas, vira `failed` com motivo —
-- visível, não silencioso.

CREATE TABLE IF NOT EXISTS integration_jobs (
    id            TEXT PRIMARY KEY,
    integration   TEXT NOT NULL,
    status        TEXT NOT NULL,     -- pending | running | done | failed | cancelled
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_attempt  TEXT,
    idempotency   TEXT,
    data          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
    ON integration_jobs(integration, idempotency)
    WHERE idempotency IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_due
    ON integration_jobs(status, next_attempt);
