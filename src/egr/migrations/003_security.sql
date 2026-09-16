-- Fase 4: identidade verificável, RBAC, cofre de segredos e gestão de chaves.
--
-- Nenhuma tabela abaixo guarda segredo em claro:
--   secrets.data       -> envelope cifrado (EGR1) + salt, nunca o valor
--   identity_tokens    -> sha256(salt || segredo), nunca o token
--   master_keys        -> apenas o key_id (impressão digital) e metadados

CREATE TABLE IF NOT EXISTS principals (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL DEFAULT 'human',
    status     TEXT NOT NULL DEFAULT 'active',
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_principals_kind   ON principals(kind);
CREATE INDEX IF NOT EXISTS idx_principals_status ON principals(status);

CREATE TABLE IF NOT EXISTS identity_tokens (
    id           TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT,
    revoked_at   TEXT,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_principal ON identity_tokens(principal_id);

CREATE TABLE IF NOT EXISTS secrets (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    provider   TEXT,
    key_id     TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_secrets_provider ON secrets(provider);

CREATE TABLE IF NOT EXISTS master_keys (
    key_id     TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    actor      TEXT,
    created_at TEXT NOT NULL,
    rotated_at TEXT
);
