-- Fase 10: Remote Control — bindings de canal e histórico de mensagens.
--
-- Telegram/Slack/Web são interfaces atrás do Gateway. Quem fala pelo canal é um
-- Principal do Runtime (pareado e com papéis), nunca "o bot".

CREATE TABLE IF NOT EXISTS gateway_bindings (
    id          TEXT PRIMARY KEY,   -- <channel>:<external_id>
    channel     TEXT NOT NULL,
    external_id TEXT NOT NULL,
    status      TEXT NOT NULL,      -- pending | active | blocked
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bindings_channel ON gateway_bindings(channel);
CREATE INDEX IF NOT EXISTS idx_bindings_status  ON gateway_bindings(status);

-- log de conversa: texto já redigido e truncado (o canal não é cofre)
CREATE TABLE IF NOT EXISTS gateway_messages (
    id         TEXT PRIMARY KEY,
    channel    TEXT NOT NULL,
    direction  TEXT NOT NULL,       -- in | out
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gateway_messages_channel ON gateway_messages(channel, created_at DESC);
