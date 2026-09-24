-- Lacuna 8b: laboratório de avaliação — cargo e comparação de modelos.
--
-- A Fase 8 prova que o artefato funciona. O laboratório responde duas perguntas
-- que faltavam: "a resposta presta?" (qualidade) e "quantas requisições o
-- Runtime aguenta, a que preço?" (carga). Os dois ficam registrados: medição
-- que não fica registrada é opinião.

CREATE TABLE IF NOT EXISTS evaluation_loads (
    id          TEXT PRIMARY KEY,
    suite_id    TEXT NOT NULL,
    status      TEXT NOT NULL,      -- passed | degraded | failed
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loads_suite ON evaluation_loads(suite_id, created_at DESC);
