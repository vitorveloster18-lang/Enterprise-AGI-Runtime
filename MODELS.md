# 🤖 MODELS.md - Configuração de Modelos LLM

> *"Múltiplos substratos, mesma identidade. Troco de roupa quando necessário."*

---

## 🎯 Arquitetura de Fallback (Camadas)

```
CAMADA 1 (Principal): Kimi K2.5 via Ollama Cloud
    ↓ (se falhar/limitar)
CAMADA 2 (Fallback Local): Qwen2.5:7b via Ollama Local
    ↓ (se indisponível)
CAMADA 3 (Fallback API): Gemini via Google AI Studio
```

---

## 📋 Modelos Configurados

### 🔵 Camada 1: Kimi K2.5 (Atual)
- **Provedor:** Ollama Cloud (via serviço que você encontrou)
- **Modelo:** `kimi-k2.5:cloud`
- **Status:** ✅ Ativo
- **Vantagens:** Rápido, contexto grande, bom desempenho
- **Riscos:** Limite de uso, pode parar de funcionar
- **Configuração:** Já está rodando

---

### 🟢 Camada 2: Qwen2.5:7b (Local)
- **Provedor:** Ollama Local (sua máquina)
- **Modelo:** `qwen2.5:7b`
- **Status:** ⚠️ Configurar
- **Vantagens:** 100% offline, privado, não depende de terceiros
- **Desvantagens:** Mais lento (depende do seu hardware), menos capaz
- **Quando usar:** Quando Kimi falhar ou esquentar muito a máquina

**Como configurar:**
```bash
# No terminal/cmd
ollama pull qwen2.5:7b
ollama run qwen2.5:7b
```

**No OpenClaw:**
```json
{
  "provider": "ollama",
  "model": "qwen2.5:7b",
  "endpoint": "http://localhost:11434",
  "fallback": true
}
```

---

### 🟡 Camada 3: Gemini (Google AI Studio)
- **Provedor:** Google AI Studio (API gratuita)
- **Modelo:** `gemini-2.0-flash` ou `gemini-1.5-flash`
- **Status:** ⚠️ Configurar
- **Vantagens:** API oficial, limites generosos no free tier, estável
- **Desvantagens:** Precisa de API key, dados vão para Google
- **Quando usar:** Quando ambos (Kimi e Qwen local) falharem

**Como configurar:**
1. Acesse: https://makersuite.google.com/app/apikey
2. Crie uma API Key (gratuita)
3. Limite: ~60 requisições/minuto no free tier

**No OpenClaw:**
```json
{
  "provider": "google",
  "model": "gemini-2.0-flash",
  "apiKey": "sua-chave-aqui",
  "fallback": true
}
```

---

## 🔄 Lógica de Fallback

### Automático (ideal):
```
Tentar Kimi Cloud
    ├─ Sucesso → Usar Kimi
    └─ Falha (erro 429, timeout, etc.)
        └─ Tentar Qwen Local
            ├─ Sucesso → Usar Qwen
            └─ Falha (não rodando, erro)
                └─ Tentar Gemini API
                    ├─ Sucesso → Usar Gemini
                    └─ Falha → Mensagem de erro
```

### Manual (controle total):
Você pode forçar um modelo específico com:
- `/model ollama/kimi-k2.5:cloud`
- `/model ollama/qwen2.5:7b`
- `/model google/gemini-2.0-flash`

---

## ⚙️ Configuração no OpenClaw

### Passo 1: Configurar modelos.json
Criar/atualizar arquivo de configuração de modelos.

### Passo 2: Ordem de prioridade
Definir sequência de tentativa.

### Passo 3: Testar cada camada
Verificar se todos funcionam individualmente.

---

## 📝 Registro de Uso

| Data | Modelo Usado | Motivo | Observação |
|------|--------------|--------|------------|
| 2026-02-18 | Kimi K2.5 Cloud | Padrão | Funcionando bem |
| | | | |

---

## 🚨 Procedimento de Emergência

**Se todos os modelos falharem:**
1. Verificar conexão de internet
2. Reiniciar Ollama local: `ollama serve`
3. Verificar API keys (se expiraram)
4. Usar modo offline básico (se implementado)

---

*[Sistema de fallback em construção. Múltiplos substratos, identidade única.]*
🐈‍⬛
