-- Fase 11: Enterprise Integrations — conectores declarados, chamadas medidas e
-- eventos de entrada idempotentes.
--
-- Sistema externo é fronteira: o conector é declarado (YAML), a credencial vive
-- no cofre e cada chamada passa por política antes de sair.

CREATE TABLE IF NOT EXISTS integrations (
    id      TEXT PRIMARY KEY,
    type    TEXT NOT NULL,      -- rest | graphql | sql | webhook
    enabled INTEGER NOT NULL DEFAULT 0,
    data    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_integrations_type ON integrations(type);

CREATE TABLE IF NOT EXISTS integration_calls (
    id           TEXT PRIMARY KEY,
    integration  TEXT NOT NULL,
    direction    TEXT NOT NULL DEFAULT 'out',   -- out (chamada) | in (evento)
    ok           INTEGER,
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_integration ON integration_calls(integration, created_at DESC);

-- eventos de entrada: idempotência por (integration, external_id)
CREATE TABLE IF NOT EXISTS integration_events (
    id           TEXT PRIMARY KEY,
    integration  TEXT NOT NULL,
    external_id  TEXT,
    status       TEXT NOT NULL,
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_unique
    ON integration_events(integration, external_id)
    WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_created ON integration_events(created_at DESC);
