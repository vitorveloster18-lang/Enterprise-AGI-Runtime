-- Fase 12: pacotes verticais instalados (o catálogo vive no pacote Python; o que
-- está instalado vive no workspace, com versão e impressão digital do conteúdo).

CREATE TABLE IF NOT EXISTS packs (
    id      TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    data    TEXT NOT NULL,
    installed_at TEXT NOT NULL
);
