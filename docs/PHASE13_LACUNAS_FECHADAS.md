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

---

## 2. Lacuna 12b — fila com alguém olhando e pack que evolui sem atropelar

**O problema:** restavam duas promessas em aberto da Fase 12 — a fila de saída
tinha `drain` (uma rodada), mas ninguém para ficar chamando; e atualizar um pack
instalado não tinha caminho: ou sobrescrevia o que alguém editou, ou travava.

### 2.1 Dreno em background (`QueueWorker`)

O worker **não é um daemon escondido dentro do Runtime**: é um laço explícito,
com intervalo declarado, parada limpa e relatório do que fez.

```bash
egr integration worker --interval 30        # olha a fila a cada 30s
egr integration worker --rounds 1           # uma rodada e sai (serve para cron)
egr integration worker --batch 5            # no máximo 5 jobs por rodada
```

| Comportamento | Regra |
|---|---|
| fila vazia | espera cresce (intervalo → dobro → teto de 300s): fila parada não martela o banco |
| fila com trabalho | volta ao intervalo base imediatamente |
| provedor fora do ar | o erro vira relatório da rodada; o laço continua |
| fila desabilitada | o worker avisa e encerra (não fica rodando à toa) |
| Ctrl+C / SIGTERM | encerra no fim da rodada, não no meio de um job |

Cada rodada chama o mesmo `ConnectorService.drain()`: mesma janela de espera,
mesma política `integration.call`, mesma auditoria. O worker só repete o que já
era governado — ele não ganha nenhum atalho.

### 2.2 Atualização de pack com conteúdo editado

`egr pack update <id>` compara o instalado com o catálogo e classifica **cada
arquivo**:

| Situação | Critério | O que acontece |
|---|---|---|
| `novo` | o arquivo não existe no workspace | entra inteiro |
| `atualizável` | existe **exatamente** como o pack escreveu | é substituído sem perder nada |
| `conflito` | existe com conteúdo diferente do registrado (alguém editou) | **preservado** por padrão; só entra com `--overwrite` |

O plano viaja dentro da proposta (`metadata.pack_update`) e é honrado na
aplicação. O manifesto (`packs/<id>.yaml`) é sempre atualizado: ele é o alvo da
própria proposta, não um arquivo qualquer.

A atualização continua sendo **proposta → aprovação → aplicação** (Fase 7), e
gera o evento `pack.updated` com `de`, `para` e a lista de `preservados`.
Preservar não é esquecer: o arquivo mantido continua registrado no
`InstalledPack`, com a impressão digital que ele tem agora.

**O que continua fora:** migração de conteúdo (reescrever o arquivo do pack
mantendo o ajuste local) e resolução de conflito interativa — o Runtime diz
onde está o conflito; quem resolve é o humano, pelo diff.

---

## 3. Lacuna 8b — laboratório de avaliação: qualidade e carga

**O problema:** a Fase 8 media o **encanamento** — passa?, custa?, demora? Duas
perguntas ficaram fora: *a resposta presta?* e *quantas requisições o Runtime
aguenta sem quebrar o governo?*

### 3.1 Qualidade (`egr eval judge`, `egr eval compare`)

Cada caso pode declarar `expected` (resposta esperada) e `expected_contains`
(trechos obrigatórios). A nota vai de 0 a 1 por dois caminhos:

| Método | Como conta | Quando usar |
|---|---|---|
| `similaridade` | metade sobreposição de vocabulário (Jaccard sem stopwords), metade trechos obrigatórios presentes | padrão, offline, custo zero, auditável |
| `modelo` | um provedor julga contra o esperado (formato `NOTA: x \| MOTIVO: y`) | quando o provedor está disponível e o custo é aceito |

**Degradação é dita, nunca escondida:** se o juiz não responde (sem provedor,
fora do ar, resposta fora do formato), a nota cai para a similaridade com
`degradado=True` no relatório e no evento `eval.judged`.

`min_quality` na suíte vira limiar: abaixo dele, a execução é **reprovada** com
o motivo explícito.

`egr eval compare <suíte> --models a,b` roda a mesma suíte em provedores
diferentes e devolve qualidade, acerto, custo e p95 de cada um — com o provedor
do agente **fixado durante o teste e devolvido ao valor original no fim**. Em
suíte que não usa modelo (ferramenta, workflow, política), o relatório diz que a
comparação repete a mesma execução, em vez de sugerir diferença inexistente.

### 3.2 Carga (`egr eval load`)

```bash
egr eval load <suíte> --requests 50 --concurrency 5
egr eval loads                # histórico
```

- cada requisição passa pelo **mesmo caminho** (política, orçamento, aprovação,
  auditoria) — carga não suspende governo;
- o relatório separa **erro** de **orçamento recusado**: estourar o teto não é
  lentidão, é o Runtime fazendo o que prometeu;
- mede p50/p95/p99, vazão (req/s), custo total e por requisição;
- veredito: `passed` · `degraded` (erros, mas o orçamento não foi o limite) ·
  `failed` (orçamento sendo atingido antes da capacidade, ou p95 acima do teto);
- cada simulação fica registrada (`evaluation_loads`) e gera `eval.load_finished`.

**O que continua fora:** teste distribuído (uma máquina só), perfis de memória
por thread e comparação automática entre modelos sem suíte declarada — o
laboratório mede o que a empresa escreveu, não adivinha o que ela quer.

---

## 4. Lacuna 9b — Promoção assinada e com quórum

A Fase 9 já impedia promoção automática: alguém com o papel certo aprovava.
Faltava responder duas perguntas que só aparecem quando dá errado:

1. **o que foi aprovado é o que foi aplicado?**
2. **uma pessoa sozinha pode promover o que afeta todo mundo?**

### 4.1 Assinatura do manifesto (`egr release sign|verify`)

```bash
egr release sign <release> --by human:vitor     # assina o conteúdo
egr release verify <release>                    # confere (sai com 1 se inválida)
```

A assinatura cobre o **manifesto canônico** do release: itens, tipo, versão,
revisão, impressão digital, ambientes de origem/destino e evidência. É
`HMAC-SHA256` do manifesto com a **chave mestra do workspace** — a mesma do
cofre de segredos. Nenhuma chave nova para guardar, nenhum segredo no release:
só o `key_id` (impressão da chave) aparece.

Mudou o release depois de assinado? A verificação diz qual impressão esperava e
qual encontrou (`mudou depois de assinado`). Rotacionou a chave? Diz que foi
assinado com outra chave. Assinatura falsificada? `assinatura não confere`.

Onde ela barra: `deploy` de ambiente que exige assinatura (`release.signature_environments`,
padrão `["production"]`) recusa com o motivo e o comando que resolve.

### 4.2 Quórum de aprovação (`egr release approvals`)

| Situação | Votos exigidos |
|---|---|
| staging (ou qualquer não-produção) | `release.min_approvals` — padrão **1** |
| produção | `release.min_approvals_production` — padrão **2** |

- cada voto guarda ator, papéis na hora do voto, nota e hora;
- **ninguém vota duas vezes**;
- com quórum de mais de um, **quem criou o release não conta** para o próprio
  quórum — dois votos exigem duas pessoas;
- **recusa é veto**: fica no histórico e o quórum nunca fecha;
- enquanto falta voto, o release continua `submitted` e o CLI mostra
  `rel_x com 1 de 2 votos — faltam 1`.

### 4.3 Aprovar uma coisa e aplicar outra

Além da assinatura, o `deploy` confere o **manifesto aprovado**: o hash votado é
guardado em `metadata.manifesto_aprovado`; se o release mudar depois da
aprovação, a promoção é recusada com `mudou depois da aprovação`, mesmo com
assinatura válida.

### 4.4 O que continua fora

Assinatura destacada (chave fora da máquina), infraestrutura de chaves pública
(PKI/certificados) e quórum por papel específico (ex.: "um voto tem de ser do
time de segurança"). Tudo configurável depois; nada disso é necessário para
garantir que **conteúdo aprovado é conteúdo aplicado**.

---

## 5. Lacuna 6b — Coordenação negociada e gatilhos de banco

A Fase 6 orquestra **o quê** roda. Faltavam duas peças: **quem** executa ser uma
escolha defensável, e **o que dispara** um workflow incluir mudança de dado.

### 5.1 Negociação entre agentes

```bash
egr task negotiate "conciliar lançamentos do dia"          # quem deve executar
egr task negotiate "resumir contrato" --capability reasoning
egr task negotiate "x" --strategy menor_fila|menor_custo|declarado
egr task handoff <task> --to <agente> --reason "motivo"    # repasse com motivo
```

Cada agente elegível declara um **lance** — custo estimado (tokens do objetivo ×
preço do provedor que ele usa) e tamanho da fila — e a estratégia escolhe:

| Estratégia | Critério |
|---|---|
| `equilibrado` (padrão) | custo normalizado **+** fila normalizada |
| `menor_custo` | só o custo estimado |
| `menor_fila` | só a fila do agente |
| `declarado` | `coordination.default_agent`, sem disputa |

Transparência: quem **não pode** executar aparece na lista com o motivo do veto
(agente desabilitado, ambiente diferente, capacidade incompatível, sem provedor)
em vez de sumir. A escolha é determinística (empate desempata por nome) e fica
registrada em `negotiations` + evento `task.negotiated`.

### 5.2 Handoff

Repassar uma task para outro agente é remédio, não hábito: exige motivo, tem
limite (`coordination.max_handoffs`, padrão 3) e, com identidade exigida, a
permissão `task.handoff`. Task em espera volta para `pending` (o agente novo
replaneja). Cada repasse fica em `task.context["handoffs"]` e no evento
`task.handoff`.

### 5.3 Gatilhos de banco

```bash
egr db trigger-add "task falhou" --on tasks --event update \
    --when "NEW.status = 'failed'" --emit db.task_failed
egr db triggers      # o que está declarado e o tamanho da fila
egr db drain         # o que o banco avisou vira evento do Runtime
egr db events        # histórico do que já foi avisado
```

O gatilho é um `CREATE TRIGGER` **de verdade** no SQLite — quem avisa é o banco,
não um processo olhando tabela de negócio. O aviso cai em `db_events`
(`processed_at` nulo) e `drain()` publica no mesmo barramento de eventos: um
workflow com `trigger: {type: event, event: db.task_failed}` começa sozinho.

**O `when` não é SQL livre.** Só entram colunas da lista branca da tabela
(`tasks`, `approvals`, `evaluation_runs`, `integration_jobs`), `NEW.`/`OLD.`
conforme o evento, literais, comparações e operadores lógicos. `; DROP TABLE …`
é recusado antes de chegar no banco, e o erro diz onde.

### 5.4 O que continua fora

Lance por modelo (o agente "se vendendo" com linguagem), leilão com
compromisso de execução e gatilho de banco em outros SGBDs (o mecanismo é
SQLite; para Postgres a mesma ideia vira `NOTIFY`/logica externa).

---

## 6. Lacuna 5b — Memória multimodal e limpeza de PII na escrita

A Fase 5 lembra, reforça e consolida texto. Faltavam as duas perguntas que só
aparecem quando a memória já está cheia: **o que não devia estar aqui** e **onde
guardar o que não é texto**.

### 6.1 Limpeza de PII na escrita

A limpeza acontece **antes de persistir**: o dado pessoal não chega ao banco, ao
vetor nem ao contexto do modelo.

```bash
egr memory scrub "cpf 123.456.789-09 e fone (51) 98888-7777"   # só confere
egr memory write "..."                                          # avisa o que saiu
```

| Reconhecido | Validação |
|---|---|
| CPF | dígitos verificadores |
| CNPJ | dígitos verificadores |
| cartão de crédito | Luhn (13–19 dígitos) |
| e-mail | formato |
| telefone | BR e internacional simples |
| CEP, RG | formato |

O que sai é substituído por marcador (`[cpf removido]`) para que a lacuna seja
visível a quem lê. O registro guarda `{tipo: contagem}` em
`metadata.pii_removido` e a trilha recebe `memory.pii_scrubbed` — **só o tipo,
nunca o valor**: ninguém reconstrói o CPF lendo a auditoria.

Configurável: `memory.scrub_pii` (padrão `true`) desliga, e `memory.pii_allow`
libera um tipo (ex.: `["e-mail"]` para e-mail corporativo).

### 6.2 Memória multimodal

```bash
egr memory add-media print.png --caption "erro 500 no painel de vendas"
egr memory media                     # teto, tipos aceitos, uso de disco
```

- o binário vai para `artifacts/media/<ano>/<impressão>.<ext>` — **fora do
  banco**, com SHA-256;
- o tipo é conferido pelos **bytes mágicos**: renomear `exe` para `png` não
  engana (`tipo não reconhecido pelos bytes`);
- teto por arquivo (`memory.max_media_bytes`, padrão 5 MiB) e lista branca de
  MIME (`memory.media_mimes`);
- o que fica **buscável** é o lado textual: legenda ou transcrição declarada por
  quem escreveu. Sem legenda, o registro existe e é recuperável por filtro, mas
  diz `[sem legenda]`;
- o binário nunca é vetorizado e nunca entra no contexto: o agente recebe a
  referência (`[imagem · erro.png · 208 B · image/png]`) e o texto.

### 6.3 O que continua fora

Visão computacional e transcrição de áudio locais: quem descreve o conteúdo é
quem escreveu, e essa descrição é declarada — o Runtime não inventa legenda nem
finge ter entendido a imagem. Também fora: busca por similaridade de imagem
(embedding visual) e deduplicação de mídia por conteúdo além da impressão
digital exata.
