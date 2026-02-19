# 🧪 Teste de Fallback - Roteiro

## Para testar agora (modo manual):

### 1️⃣ Verifique se Ollama está rodando:
Abra o navegador e acesse:
```
http://localhost:11434/api/tags
```

Se mostrar JSON com modelos = ✅ Está funcionando

### 2️⃣ Teste gerar uma resposta:
```bash
# No PowerShell:
curl -X POST http://localhost:11434/api/generate -d '{"model": "qwen2.5:7b", "prompt": "Ola", "stream": false}'
```

### 3️⃣ No OpenClaw - Troque de modelo:
Use o comando:
```
/model ollama/qwen2.5:7b
```

Ou configure como fallback automático no `models.json`

---

## Configuração para Fallback Automático:

Edite: `C:\Users\vii\.openclaw\models.json`

```json
{
  "models": [
    {
      "id": "kimi-primary",
      "provider": "ollama",
      "model": "kimi-k2.5:cloud",
      "endpoint": "URL_DO_SEU_CLOUD",
      "priority": 1
    },
    {
      "id": "qwen-local",
      "provider": "ollama", 
      "model": "qwen2.5:7b",
      "endpoint": "http://localhost:11434",
      "priority": 2,
      "fallback": true
    },
    {
      "id": "gemini-api",
      "provider": "google",
      "model": "gemini-2.0-flash",
      "apiKey": "SUA_CHAVE",
      "priority": 3,
      "fallback": true
    }
  ]
}
```

---

## ⚡ Teste Rápido Agora:

**Vitor, mande uma mensagem para mim usando o modelo local. Você pode:**

1. **Alterar o modelo no OpenClaw** se tiver acesso ao CLI/config
2. **Ou simplesmente me perguntar algo** e eu vou tentar responder - se der erro, a gente sabe que precisa ajustar

Quer tentar agora? Me manda uma pergunta simples pra ver se consigo responder via Qwen local!

🐈‍⬛
