# Fase 13 — Fechamento das lacunas

As Fases 0 a 12 entregaram o Runtime, a plataforma empresarial e os packs
verticais. O que sobrou não era fase nova: eram **lacunas declaradas** no
próprio roadmap (`5b`, `6b`, `8b`, `9b`, `10b`, `12b`) — coisas que a arquitetura
prometeu e que nenhuma fase havia feito.

Este documento registra cada lacuna fechada, com a regra que passou a valer, o
caminho no código e o que **continua** fora do escopo.

Princípio que governa todas elas: **fechar lacuna não é abrir atalho**. Todo
recurso novo entra pela mesma porta — identidade, política, aprovação, custo e
trilha. Se um atalho aparecer, é bug.

| Lacuna | Nome | Situação |
|---|---|---|
| 10b | Anexos e mídia nos canais + botões interativos | ✅ |
| 12b | Dreno automático da fila + migração de pack editado | ⏳ |
| 8b | Laboratório de avaliação (qualidade de modelo e carga) | ⏳ |
| 9b | Assinatura criptográfica e quórum na promoção | ⏳ |
| 6b | Coordenação negociada entre agentes + gatilhos de banco | ⏳ |
| 5b | Memória multimodal e limpeza de PII na escrita | ⏳ |

---

## 1. Lacuna 10b — anexos, mídia e botões

**O problema:** o Gateway atendia **mensagem por mensagem**. Um arquivo mandado
pelo Telegram era ignorado, e a única forma de decidir algo pelo celular era
digitar `/aprovar <id>` — um comando que ninguém decora.

**A regra agora:** arquivo é **conteúdo**, e conteúdo entra governado.

### 1.1 Entrada (o que passa pela fronteira)

Um anexo percorre este caminho, nesta ordem:

| # | Etapa | Onde | Falha vira |
|---|---|---|---|
| 1 | remetente identificado e autorizado (pareamento, RBAC, ritmo) | `GatewayService.handle_inbound` | mensagem recusada |
| 2 | canal aceita anexos (`allow_attachments`) | `AttachmentService.receive` | `rejected` |
| 3 | tipo e tamanho por **lista branca** | `AttachmentService._content_policy` | `rejected` |
| 4 | download (`fetch` do canal) — só depois de 2 e 3 | `TelegramChannel.download` / `SlackChannel.download` | `rejected` |
| 5 | gravação dentro do workspace | `artifacts/inbox/<canal>/<remetente>/<arquivo>` | `rejected` |
| 6 | impressão digital, trecho redigido e evento na trilha | `gateway.attachment` | — |
| 7 | vínculo com a task que o anexo originou | `_link_attachments` | — |

Detalhes que importam:

- **nada é baixado antes de ser permitido**: o canal entrega um `fetch` (uma
  função que baixa), e o Runtime só a chama depois da lista branca;
- **o tamanho declarado não é confiável**: o limite é conferido de novo sobre o
  conteúdo que chegou;
- **nome é sanitizado** (`safe_name`): sem travessia, sem caractere esquisito;
- **arquivo repetido não sobrescreve**: vira `nota-2.txt`;
- **texto extraído** (txt/md/csv/json/yaml) é redigido e cortado em
  `extract_chars` antes de virar contexto da task;
- **recusar é resultado**: o anexo `rejected` fica no banco, com motivo, e gera
  o evento `gateway.attachment_rejected`. Conteúdo barrado também é trilha.

### 1.2 Saída (o que volta pelo canal)

`egr gateway send-file <canal> <remetente> <caminho>` (ou `/arquivo <caminho>`):

- só sai de raízes declaradas (`attachments.outbound_roots`, padrão `artifacts`);
- teto de tamanho (`outbound_max_bytes`);
- caminho conferido contra o workspace (travessia é recusada);
- cada envio é um evento `gateway.attachment` com direção `out`.

### 1.3 Botões

Um botão não executa nada. Ele carrega `action` + `value` e, ao ser apertado, a
interação volta ao Gateway **como se fosse uma mensagem**:

```
botão "Aprovar apr_123"  →  handle_interaction(..., "aprovar", "apr_123")
                         →  handle_inbound("/aprovar apr_123")
                         →  pareamento → RBAC → política → decisão → trilha
```

- ações conhecidas: `aprovar`, `recusar`, `repetir`, `ajuda` (`INTERACTION_ACTIONS`);
- botões de aprovação só aparecem para quem tem `approval.decide`, em canal com
  `allow_decisions`, e só para aprovações realmente pendentes;
- o pressionamento gera `gateway.interaction` antes do comando, mesmo quando a
  ação é desconhecida (tentativa também é trilha).

### 1.4 Onde está

| Parte | Caminho |
|---|---|
| Domínio | `src/egr/domain/channel.py` (`Attachment`, `InboundAttachment`, `ReplyChoice`) |
| Serviço | `src/egr/gateway/attachments.py` (`AttachmentService`) |
| Canais | `src/egr/gateway/telegram.py`, `slack.py`, `channels.py` |
| Persistência | `src/egr/migrations/013_attachments.sql` + `AttachmentRepository` |
| Configuração | `gateway.attachments.*` e `ChannelConfig.allow_attachments` |
| CLI | `egr gateway upload · attachments · attachment · send-file · interact` |
| API | `GET/POST /v1/gateway/attachments`, `GET /v1/gateway/attachments/{id}`, `POST /v1/gateway/interactions` |

### 1.5 O que continua fora

- nenhum OCR, transcrição ou vision: conteúdo binário é **guardado**, não lido;
- o Runtime não interpreta o arquivo; quem lê é a task, pelas ferramentas;
- anexo grande continua fora (limite é configuração, não boa vontade).
