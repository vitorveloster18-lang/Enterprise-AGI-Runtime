-- Fase 5: memória semântica (vetores locais) e ciclo de vida da memória.
--
-- O vetor NÃO é o conteúdo: é uma projeção determinística (feature hashing)
-- usada apenas para similaridade. O texto continua sendo a fonte da verdade.
-- `model` identifica o algoritmo/dimensão: trocar de backend exige reindexar,
-- e registros sem vetor continuam funcionando (caem no lado léxico da busca).

CREATE TABLE IF NOT EXISTS memory_vectors (
    id         TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    data       BLOB NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_vectors_model ON memory_vectors(model);
