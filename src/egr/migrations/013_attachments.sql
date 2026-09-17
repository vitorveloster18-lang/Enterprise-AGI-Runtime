-- Lacuna 10b: anexos e mídia dos canais.
--
-- Um anexo é conteúdo que entrou por uma interface (Telegram/Slack/Web) e virou
-- arquivo do workspace. O registro guarda o caminho, o checksum e a decisão —
-- inclusive quando a decisão foi recusar: conteúdo barrado também é trilha.

CREATE TABLE IF NOT EXISTS gateway_attachments (
    id          TEXT PRIMARY KEY,
    channel     TEXT NOT NULL,
    external_id TEXT NOT NULL,
    status      TEXT NOT NULL,      -- received | stored | rejected
    name        TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_channel ON gateway_attachments(channel, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_attachments_sender  ON gateway_attachments(channel, external_id, created_at DESC);
