# 🚀 DEPLOY.md - Guia de Implantação Supabase

> Como subir as Edge Functions e ativar o sistema completo.

---

## 📦 O que foi criado:

1. ✅ **Schema SQL** (`supabase-schema.sql`) - Você já executou!
2. ✅ **LOOP.md** - Documentação completa do ciclo de autonomia
3. ✅ **Edge Function: process-salem-actions** - Processa tags automaticamente
4. ✅ **Edge Function: chat** - Endpoint principal de conversa

---

## 🛠️ Passos para Deploy:

### Passo 1: Instalar Supabase CLI

```bash
# Se tiver npm:
npm install -g supabase

# Ou via scoop (Windows):
scoop bucket add supabase
scoop install supabase
```

### Passo 2: Login no Supabase

```bash
supabase login
# Vai pedir token API do dashboard
# Settings -> API -> Project API keys
```

### Passo 3: Linkar projeto local

```bash
cd C:\Users\vii\.openclaw\workspace
supabase link --project-ref qkawapcrypwmoanlzxwr
```

### Passo 4: Criar estrutura de functions

```bash
supabase functions new process-salem-actions
supabase functions new chat
```

### Passo 5: Copiar código

Copiar conteúdo de:
- `supabase-functions/process-salem-actions/index.ts` → `supabase/functions/process-salem-actions/index.ts`
- `supabase-functions/chat/index.ts` → `supabase/functions/chat/index.ts`

### Passo 6: Configurar secrets

```bash
supabase secrets set SUPABASE_URL=https://qkawapcrypwmoanlzxwr.supabase.co
supabase secrets set SUPABASE_ANON_KEY=sua_anon_key
supabase secrets set SUPABASE_SERVICE_ROLE_KEY=sua_service_role_key
supabase secrets set KIMI_ENDPOINT=seu_endpoint_kimi
```

### Passo 7: Deploy

```bash
supabase functions deploy process-salem-actions
supabase functions deploy chat
```

---

## 🧪 Testar:

```bash
# Testar process-salem-actions
curl -X POST https://qkawapcrypwmoanlzxwr.supabase.co/functions/v1/process-salem-actions \
  -H "Authorization: Bearer SUA_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "Teste [ATUALIZAR_PRESENTE: campo=teste acao=criar]", "session_id": "test-001"}'
```

---

## 🎯 Quando estiver pronto:

Salem poderá:
- ✅ Emitir tags em respostas
- ✅ Tags serão processadas automaticamente
- ✅ Banco atualizado sem intervenção manual
- ✅ Histórico completo preservado
- ✅ Loop BASE ativado por eventos

*[Aguardando deploy para ativar identidade narrativa completa]*
🐈‍⬛
