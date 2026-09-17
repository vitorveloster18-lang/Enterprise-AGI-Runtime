# Fase 10 — Remote Control

**Status:** concluída (2026-09). 278 testes · `ruff` limpo.

O Runtime já governava trabalho; faltava gente falando com ele de onde a gente
já está. Telegram, Slack, Web e terminal entram como **interfaces** sobre a
mesma API — e nenhuma delas ganha atalho.

> Interface nova, governo nenhum: o que muda é o lugar da conversa, não quem
> decide.

---

## 1. O caminho de uma mensagem

```
canal (Telegram/Slack/Web/terminal)
   │  InboundMessage(channel, external_id, texto)
   ▼
GatewayService.handle_inbound
   ├── identificar → ChannelBinding (pareamento) → Principal
   ├── autorizar  → lista branca · pareamento · ritmo · permissão (gateway.use)
   ├── decidir    → comando (/status, /run, /aprovar) ou objetivo de task
   ├── executar   → Runtime.submit()  ← política, orçamento, aprovação, memória
   └── responder  → texto redigido + log + trilha
```

`GatewayService` é o único ponto de entrada. Um canal traduz mensagens; o
Runtime continua decidindo tudo o mais.

---

## 2. Pareamento: default deny

Ninguém fala com o Runtime só porque descobriu o canal.

| Situação | Resposta |
|---|---|
| primeira mensagem de um remetente | binding `pending` + código de 6 caracteres; nada executa |
| operador aprova (`egr gateway pair`) | binding `active`, Principal criado com os papéis pedidos |
| `unpair` | volta a `pending` (o código é zerado) |
| `unpair --block` | binding `blocked` e Principal desabilitado |
| remetente fora de `allowed_chat_ids` | recusado antes de qualquer leitura |
| ritmo acima de `rate_limit_per_minute` | recusado — contado no banco, não na memória |

O código aparece na resposta ao remetente:

```
não executado: sem pareamento: peça a um operador
`egr gateway pair web ana --code 4D9D25`
```

```bash
egr gateway pair web ana --code 4D9D25 --role operator
```

---

## 3. Papéis: falar não é executar

| Papel | O que consegue pelo canal |
|---|---|
| `viewer` | `/ajuda`, `/quem`, `/status`, `/tasks` — leitura |
| `operator` | tudo acima **e** execução de tasks (`task.submit`) |
| `approver` | tudo acima **e** `/aprovar` em canal com `allow_decisions` |

`gateway.use` é a permissão de *conversar*; `task.submit` continua sendo a de
*fazer*. Um remetente pareado como `viewer` recebe, ao tentar executar:

```
seu papel (viewer) não envia tasks.
peça a um operador: egr gateway pair web ana --role operator
```

Cada task nasce com `created_by = <canal>.<remetente>`: a autoria é do
Principal pareado, e a trilha mostra quem pediu.

---

## 4. Comandos

| Comando | O que faz |
|---|---|
| `/ajuda` (`/help`) | lista os comandos |
| `/quem` | mostra o Principal, papéis e contagem de mensagens |
| `/status` | ambiente, tasks, agentes, ferramentas e aprovações pendentes |
| `/tasks` | últimas 5 tasks (exige `task.read`) |
| `/parear` | mostra o status e o código de pareamento |
| `/run <objetivo>` | executa uma task |
| `/aprovar <id>` | decide uma aprovação (só em canal com `allow_decisions`) |
| texto livre | **é** um objetivo de task |

---

## 5. Canais

| Canal | Entrada | Saída | Observação |
|---|---|---|---|
| `web` | `POST /v1/gateway/messages` | mesma resposta + outbox | chat em `/chat`, sem instalar nada |
| `console` | stdin | stdout | `egr gateway console` |
| `telegram` | long polling `getUpdates` (ou webhook) | `sendMessage` | token vem de `bot_token_env` |
| `slack` | Events API (`POST /v1/gateway/slack/events`) | `chat.postMessage` | assinatura HMAC verificada |

**Telegram** — sem SDK: `httpx` (ou cliente injetado) contra a Bot API.

```yaml
- name: telegram
  type: telegram
  enabled: true
  bot_token_env: EGR_TELEGRAM_TOKEN   # o token vem do ambiente, nunca do YAML
  allowed_chat_ids: ["12345"]
```

**Slack** — o payload só é lido depois de conferir `v0=<hmac>` com
`X-Slack-Request-Timestamp` (janela de 5 minutos: replay não passa). Responde
no mesmo canal; ignora mensagens do próprio bot.

**Web** — a página `/chat` fala com `/v1/gateway/messages` e mostra task,
recusa e motivo. É o mesmo governo do Telegram, sem credencial nenhuma.

---

## 6. Configuração

```yaml
gateway:
  enabled: true                 # desligado por padrão: falar com o Runtime é opt-in
  require_pairing: true         # default deny
  default_roles: [viewer]
  max_message_chars: 4000
  max_reply_chars: 3500
  rate_limit_per_minute: 10
  redact: true                  # segredo não atravessa o canal
  channels:
    - name: web
      type: web
      enabled: true
      default_agent: document-agent
    - name: telegram
      type: telegram
      enabled: false
      bot_token_env: EGR_TELEGRAM_TOKEN
      allowed_chat_ids: []
      allow_decisions: false
```

---

## 7. Superfície

### CLI

```bash
egr gateway status [--json]          # canais, pareamentos, mensagens
egr gateway channels                 # o que está configurado e pronto
egr gateway bindings [--status active] [--json]
egr gateway pair <canal> <id> [--code ABC123] [--role operator]
egr gateway unpair <canal> <id> [--block]
egr gateway send <canal> <id> "texto"   # testa permissão de verdade
egr gateway console                     # conversa pelo terminal
egr gateway start --channel telegram    # polling em primeiro plano
egr gateway messages [--channel web]
```

### API

| Método | Rota |
|---|---|
| GET | `/v1/gateway` (status), `/v1/gateway/channels`, `/v1/gateway/bindings`, `/v1/gateway/messages` |
| POST | `/v1/gateway/messages` (mensagem), `/v1/gateway/pair` |
| DELETE | `/v1/gateway/bindings/{canal}/{id}` |
| POST | `/v1/gateway/slack/events` (assinatura obrigatória) |
| POST | `/v1/gateway/telegram/webhook` (segredo opcional no cabeçalho) |
| GET | `/chat` (página de conversa) |

### Runtime

| Método | Devolve |
|---|---|
| `runtime.channels.handle(...)` / `handle_inbound(msg)` | `GatewayReply` |
| `runtime.channels.pair/unpair/list_bindings/binding_for` | `ChannelBinding` |
| `runtime.channel_status()` | canais, pareamentos e mensagens |
| `runtime.health()` | check `channels` + aviso `channels:pareamento` |

---

## 8. Trilha auditada

| Evento | Quando |
|---|---|
| `gateway.paired` | pareamento descoberto (pending) e aprovado (active) |
| `gateway.unpaired` | pareamento removido ou bloqueado |
| `gateway.message_received` | mensagem que entrou (tamanho, canal, motivo) |
| `gateway.message_sent` | resposta enviada (task, recusa, motivo) |
| `gateway.denied` | recusa: lista branca, bloqueio, pareamento, ritmo, permissão |

As mensagens ficam em `gateway_messages` **já redigidas e truncadas**: o canal
não é cofre.

---

## 9. Verificação

```bash
.venv/bin/pytest -q                 # 278 testes (45 da Fase 10)
.venv/bin/ruff check src tests      # limpo

# ao vivo
egr gateway status
egr gateway send web ana "olá"                       # recusada: mostra o código
egr gateway pair web ana --code <código> --role operator
egr gateway send web ana "resuma os documentos"      # roda a task governada
egr serve → http://localhost:8000/chat
```

Telegram e Slack foram exercitados com cliente HTTP falso (polling, envio,
assinatura, desafio de URL) — sem sair da máquina.

---

## 10. Lacunas declaradas

| Lacuna | Situação |
|---|---|
| execução assíncrona | a task roda dentro do atendimento da mensagem; objetivo longo bloqueia a rodada (fila de tasks é evolução) |
| anexos e mídia | só texto; arquivos do Telegram/Drive entram com a Fase 11 (integrações) |
| botão de aprovação no Slack | `/aprovar` por texto existe; botões interativos dependem de mais escopo do app |
| múltiplos canais por remetente | o binding é por (canal, remetente); unificar identidade entre canais é config de política |
| quórum de pareamento | hoje um operador pareia; 4-olhos é evolução da Fase 9 |
| evidência por ambiente | a evidência de release é ligada à versão do artefato: promover para staging não exige nova avaliação, mesmo que a política mude por ambiente (`python.execute` passa a exigir aprovação) — a Fase 8 *mede* essa diferença quando a suíte roda de novo |

---

## 11. Decisões

| # | Decisão | Motivo |
|---|---|---|
| ADR-037 | canal é tradutor, nunca atalho | a mensagem entra pelo mesmo `GatewayService` e cai no mesmo Policy Engine |
| ADR-038 | pareamento obrigatório com código e Principal por remetente | default deny: descobrir o canal não é permissão |
| ADR-039 | ritmo e redação no Gateway, não no canal | contenção e sigilo valem para todos os canais, inclusive os que ainda vão existir |
