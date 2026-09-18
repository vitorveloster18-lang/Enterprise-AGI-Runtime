-- Lacuna 6b: coordenação negociada entre agentes e gatilhos de banco.
--
-- Negociação: quem disputou, quanto custava, quem ganhou e por quê — escolha
--              de agente é decisão, e decisão se registra.
-- Gatilho:    a definição vive aqui (`db_triggers`) e o SQL que avisa é criado
--             de verdade no banco; o que ele avisa cai em `db_events`, que o
--             Runtime drena. Nada de polling em tabela de negócio.

CREATE TABLE IF NOT EXISTS negotiations (
    id          TEXT PRIMARY KEY,
    objective   TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    chosen      TEXT,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_negotiations_created ON negotiations(created_at DESC);

CREATE TABLE IF NOT EXISTS db_triggers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    event       TEXT NOT NULL,      -- insert | update | delete
    when_expr   TEXT NOT NULL DEFAULT '',
    emit        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_db_triggers_table ON db_triggers(table_name, event);

CREATE TABLE IF NOT EXISTS db_events (
    id           TEXT PRIMARY KEY,
    trigger_id   TEXT NOT NULL,
    event        TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    row_id       TEXT,
    payload      TEXT NOT NULL,
    processed_at TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_db_events_pending ON db_events(processed_at, created_at);
