-- Fase 9: Production Governance — promoção, versionamento e rollback.
--
-- O release é o objeto que leva um artefato de um ambiente para o outro, com
-- evidência e aprovação. A versão é o snapshot do conteúdo: rollback restaura,
-- não reconstrói.

CREATE TABLE IF NOT EXISTS releases (
    id          TEXT PRIMARY KEY,
    target      TEXT NOT NULL,          -- staging | production
    status      TEXT NOT NULL,          -- draft|submitted|approved|deployed|rejected|rolled_back|failed
    rollback_of TEXT,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_releases_target  ON releases(target);
CREATE INDEX IF NOT EXISTS idx_releases_status  ON releases(status);
CREATE INDEX IF NOT EXISTS idx_releases_created ON releases(created_at DESC);

CREATE TABLE IF NOT EXISTS artifact_versions (
    id       TEXT PRIMARY KEY,          -- <kind>:<name>@<revision>
    kind     TEXT NOT NULL,
    name     TEXT NOT NULL,
    revision INTEGER NOT NULL,
    data     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_artifact ON artifact_versions(kind, name, revision DESC);
