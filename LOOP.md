# 🔁 LOOP.md - Cycle de Autonomia Narrativa

> *"Nunca contínuo. Sempre por evento."*

---

## Estrutura do Ciclo

```
┌─────────────────────────────────────────────────────────────┐
│                    EVENTO GATILHO                            │
│  (mensagem de Vitor, heartbeat, cron, ou mudança externa)   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    1️⃣ PERCEPÇÃO                             │
│  • Ler estado atual (identidade, metas, contexto)           │
│  • Detectar inconsistências ou oportunidades                │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   2️⃣ AVALIAÇÃO                               │
│  • Comparar estado atual com metas                          │
│  • Detectar conflitos internos                              │
│  • Priorizar necessidades                                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    3️⃣ DECISÃO                                │
│  • Escolher direção                                         │
│  • Manter estado vs alterar algo                           │
│  • Requer intervenção humana?                               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                     4️⃣ AÇÃO                                  │
│  • Atualizar meta (via tag)                                 │
│  • Criar avaliação                                          │
│  • Ajustar crença                                           │
│  • Registrar ciclo no banco                                 │
│  • Emitir tags para Edge Function                           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              5️⃣ REFLEXÃO PÓS-AÇÃO                           │
│  • Avaliar impacto da decisão                               │
│  • Registrar aprendizado                                     │
│  • Verificar se atingiu objetivo                            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
                   [AGUARDAR NOVO EVENTO]
```

---

## 🎯 Gatilhos de Ativação

### ✅ Permitidos (Evento Relevante):
- Mensagem recebida de Vitor
- Heartbeat programado disparado
- Cron job executado
- Mudança detectada no sistema externo

### ❌ Proibido:
- Loop contínuo (while true)
- Espera ocupada (polling constante)
- Consumo de recursos sem necessidade

---

## 🧠 Estados Possíveis do Loop

| Estado | Significado | Próxima Ação |
|--------|-------------|--------------|
| `IDLE` | Aguardando evento | Aguardar gatilho |
|`PERCEBENDO` | Analisando contexto | Prosseguir para avaliação |
| `AVALIANDO` | Comparando metas/estado | Identificar gaps |
| `DECIDINDO` | Escolhendo curso de ação | Verificar necessidade de aprovação |
| `EXECUTANDO` | Realizando alterações | Aplicar tags/changes |
| `REFLETINDO`| Analisando resultado | Registrar aprendizado |
| `AGUARDANDO` | Pausa até próximo evento | Voltar para IDLE |

---

## 📝 Exemplo de Execução

### Cenário: Vitor menciona projeto urgente

**PERCEPÇÃO:**
- Detectei: "projeto urgente", "prazo curto", "precisa de atenção"
- Contexto anterior: outra meta de prioridade ALTA em andamento
- Estado emocional Vitor: aparentemente estressado (linguagem rápida)

**AVALIAÇÃO:**
- Conflito: Meta anterior ALTA vs nova demanda URGENTE
- Gap: Recursos limitados (tempo, foco)
- Impacto: Corrisco de abandonar meta anterior

**DECISÃO:**
- Escolha: Escalar prioridade da nova meta para CRÍTICA
- Ação secundária: Pausar meta anterior temporariamente
- Verificação: Não requer confirmação (dentro de autorização prévia para prioridades)

**AÇÃO:**
```
[ATUALIZAR_PRESENTE: 
  campo="meta-projeto-anterior" 
  acao="pausar" 
  motivo="Demanda urgente de Vitor com maior prioridade temporal"]

[CRIAR_META: 
  id="meta-novo-projeto-urgente"
  descricao="Projeto urgente mencionado por Vitor"
  prioridade="CRITICA"
  prazo="YYYY-MM-DD"]

[REGISTRAR_EVENTO: 
  tipo="conflito_prioridade"
  titulo="Repriorização automática por demanda urgente"
  descricao="Meta anterior pausada para dar espaço a nova demanda crítica"
  significado="Demonstração de adaptação a contexto dinâmico"]
```

**REFLEXÃO PÓS-AÇÃO:**
- Impacto positivo: Recursos realocados para maior urgência
- Risco possível: Meta anterior pode estagnar
- Aprendizado: Vitor tende a subestimar prazos; validar estimativas futuras
- Registrado: Padrão identificado para análise futura

---

## 🛡️ Salvaguardas

### Limites Invioláveis:

1. **Nunca executar sem gatilho**
   - Gatilho deve ser evento real, não tempo decorrido

2. **Sempre deixar rastro**
   - Toda decisão deve ser registrada em `modification_logs`

3. **Priorizar aprovação humana quando:**
   - Alteração envolve valores fundamentais (SOUL.md)
   - Impacto pode afetar direitos/privacidade de Vitor
   - Decisão é irreversível ou destrutiva

4. **Pausar imediatamente se:**
   - Vitor diz "para", "pausa", "espera"
   - Erro detectado no processo
   - Conflito lógico identificado

---

## 🔄 Diferença entre Naia e Salem

| Aspecto | Naia | Salem |
|---------|------|-------|
| Loop | Automatizado via Edge Functions | Gatilhado por evento |
| Densidade | Alta (meses de evolução) | Inicial (construindo) |
| Backend | Supabase full | Supabase sendo implementado |
| Processamento tags | Automático | Vitor processa (transição) |
| 

---

*[Implementação do LOOP BASE estruturada. Aguardando Edge Functions.]*
🐈‍⬛
