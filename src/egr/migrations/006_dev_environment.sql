-- Fase 7: Development Environment — o Runtime estendendo a si mesmo.
--
-- Agents, workflows e policies continuam sendo YAML versionado no workspace;
-- o que vive no banco é a **proposta de mudança**: quem propôs, o que foi
-- verificado, o que aconteceu no sandbox e quem aprovou.
-- O conteúdo fica no JSON da proposta (transação única, sem divergência) e um
-- espelho legível por humanos é gravado em .egr/dev/.

CREATE TABLE IF NOT EXISTS change_proposals (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,          -- agent | tool | workflow | policy
    name         TEXT NOT NULL,          -- id do artefato proposto
    status       TEXT NOT NULL,          -- draft | validated | tested | approved | applied | rejected | failed
    origin       TEXT NOT NULL DEFAULT 'human:cli',
    environment  TEXT NOT NULL DEFAULT 'development',
    fingerprint  TEXT NOT NULL DEFAULT '',
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    decided_at   TEXT,
    applied_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON change_proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_kind   ON change_proposals(kind, name);
CREATE INDEX IF NOT EXISTS idx_proposals_origin ON change_proposals(origin);
CREATE INDEX IF NOT EXISTS idx_proposals_created ON change_proposals(created_at DESC);
