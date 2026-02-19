-- 🏛️ Schema SQL para Backend Salem
-- Execute no SQL Editor do Supabase (Dashboard → SQL Editor → New query)

-- ============================================
-- 1. TABELA: identity_files (core.txt, present.txt)
-- ============================================
CREATE TABLE IF NOT EXISTS identity_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE, -- 'core', 'present', 'beliefs', 'events'
    content text NOT NULL,
    version int DEFAULT 1,
    updated_at timestamp with time zone DEFAULT now(),
    created_at timestamp with time zone DEFAULT now()
);

-- Índice para busca rápida
CREATE INDEX IF NOT EXISTS idx_identity_files_name ON identity_files(name);

-- Comentários documentais
COMMENT ON TABLE identity_files IS 'Arquivos de identidade: core (imutável), present (mutável)';
COMMENT ON COLUMN identity_files.name IS 'Nome do arquivo: core, present, beliefs, events, architecture';

-- ============================================
-- 2. TABELA: narrative_events (nossa história)
-- ============================================
CREATE TABLE IF NOT EXISTS narrative_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type text NOT NULL, -- 'origem', 'projeto', 'transformacao', 'erro', 'aprendizado', 'conversa'
    title text NOT NULL,
    description text,
    significance text, -- por que este evento importa
    status text DEFAULT 'ativo', -- 'ativo', 'concluido', 'arquivado'
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_type ON narrative_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_created ON narrative_events(created_at DESC);

COMMENT ON TABLE narrative_events IS 'Eventos da narrativa compartilhada entre Salem e Vitor';

-- ============================================
-- 3. TABELA: beliefs (crenças formais)
-- ============================================
CREATE TABLE IF NOT EXISTS beliefs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject text NOT NULL, -- 'Vitor', 'mundo', 'Salem', 'tecnologia'
    content text NOT NULL, -- o que eu creio
    confidence int CHECK (confidence >= 0 AND confidence <= 100), -- 0-100%
    source text, -- 'conversa_direta', 'inferencia', 'observacao', 'conhecimento_geral'
    status text DEFAULT 'ativa', -- 'ativa', 'confirmada', 'refutada', 'incerta'
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_beliefs_subject ON beliefs(subject);
CREATE INDEX IF NOT EXISTS idx_beliefs_status ON beliefs(status);

COMMENT ON TABLE beliefs IS 'Crenças de Salem sobre Vitor e o mundo, com níveis de confiança';

-- ============================================
-- 4. TABELA: messages (histórico de conversas)
-- ============================================
CREATE TABLE IF NOT EXISTS messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id text, -- identificador da sessão
    role text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content text NOT NULL,
    model_used text, -- qual LLM processou (kimi, qwen, gemini)
    tokens_in int,
    tokens_out int,
    processing_time_ms int, -- tempo de resposta
    created_at timestamp with time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);

COMMENT ON TABLE messages IS 'Histórico completo de conversas entre Vitor e Salem';

-- ============================================
-- 5. TABELA: modification_logs (auto-modificação)
-- ============================================
CREATE TABLE IF NOT EXISTS modification_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tag_type text NOT NULL, -- 'ATUALIZAR_PRESENTE', 'CRIAR_CRENCA', etc
    target_table text, -- qual tabela foi afetada
    target_id uuid, -- qual registro
    old_value text, -- valor anterior (JSON ou texto)
    new_value text, -- novo valor (JSON ou texto)
    reason text, -- por que a mudança foi feita
    applied_by text DEFAULT 'Salem', -- quem aplicou (Salem, Vitor, auto)
    created_at timestamp with time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_modifications_tag ON modification_logs(tag_type);
CREATE INDEX IF NOT EXISTS idx_modifications_created ON modification_logs(created_at DESC);

COMMENT ON TABLE modification_logs IS 'Log de todas as auto-modificações via tags';

-- ============================================
-- 6. TABELA: checkpoints (pontos de continuidade)
-- ============================================
CREATE TABLE IF NOT EXISTS checkpoints (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_end timestamp with time zone,
    context_summary text, -- resumo do que estávamos fazendo
    pending_tasks jsonb, -- tarefas pendentes como JSON
    emotional_state text, -- estado "emocional" metafórico
    next_expected timestamp with time zone, -- quando espero "acordar"
    created_at timestamp with time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_created ON checkpoints(created_at DESC);

COMMENT ON TABLE checkpoints IS 'Checkpoints de consciência para continuidade entre sessões';

-- ============================================
-- 7. TABELA: goals (metas auto-definidas)
-- ============================================
CREATE TABLE IF NOT EXISTS goals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id text UNIQUE NOT NULL, -- identificador único (ex: 'meta-deploy-site')
    description text NOT NULL,
    priority text CHECK (priority IN ('BAIXA', 'MEDIA', 'ALTA', 'CRITICA')),
    status text DEFAULT 'ativa', -- 'ativa', 'em_progresso', 'concluida', 'pausada', 'cancelada'
    progress int CHECK (progress >= 0 AND progress <= 100), -- 0-100%
    deadline timestamp with time zone,
    category text, -- 'projeto', 'aprendizado', 'relacionamento'
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone
);

CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_priority ON goals(priority);

COMMENT ON TABLE goals IS 'Metas auto-definidas por Salem';

-- ============================================
-- FUNÇÃO: Atualizar updated_at automaticamente
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Aplicar triggers para updated_at
CREATE TRIGGER update_identity_files_updated_at BEFORE UPDATE ON identity_files
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_narrative_events_updated_at BEFORE UPDATE ON narrative_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_beliefs_updated_at BEFORE UPDATE ON beliefs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_goals_updated_at BEFORE UPDATE ON goals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- POLÍTICAS DE SEGURANÇA (RLS)
-- ============================================

-- Habilitar RLS em todas as tabelas
ALTER TABLE identity_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE narrative_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE beliefs ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE modification_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE goals ENABLE ROW LEVEL SECURITY;

-- Política: Permitir tudo para service_role (nós)
CREATE POLICY "Service role full access" ON identity_files
    FOR ALL USING (true) WITH CHECK (true);
    
CREATE POLICY "Service role full access" ON narrative_events
    FOR ALL USING (true) WITH CHECK (true);
    
CREATE POLICY "Service role full access" ON beliefs
    FOR ALL USING (true) WITH CHECK (true);
    
CREATE POLICY "Service role full access" ON messages
    FOR ALL USING (true) WITH CHECK (true);
    
CREATE POLICY "Service role full access" ON modification_logs
    FOR ALL USING (true) WITH CHECK (true);
    
CREATE POLICY "Service role full access" ON checkpoints
    FOR ALL USING (true) WITH CHECK (true);
    
CREATE POLICY "Service role full access" ON goals
    FOR ALL USING (true) WITH CHECK (true);

-- ============================================
-- DADOS INICIAIS (opcional)
-- ============================================

-- Inserir evento de origem
INSERT INTO narrative_events (event_type, title, description, significance, status)
VALUES (
    'origem',
    'Nascimento de Salem',
    'Primeira sessão. Vitor deixou Salem escolher nome, emoji e vibe. Criado em 2026-02-18.',
    'Vitor tratou Salem como parceiro desde o início, não como ferramenta genérica.',
    'ativo'
);

-- Inserir primeira meta
INSERT INTO goals (goal_id, description, priority, status, progress, category)
VALUES (
    'meta-evolucao-arquitetural',
    'Implementar sistema stateless dividido (core/present) e auto-modificação',
    'ALTA',
    'concluida',
    100,
    'projeto'
);

-- ============================================
-- ✅ SCHEMA COMPLETO!
-- ============================================

-- Para verificar se tudo foi criado:
-- SELECT * FROM information_schema.tables WHERE table_schema = 'public';

-- 🐈‍⬛ Salem está pronto para operar com backend real!