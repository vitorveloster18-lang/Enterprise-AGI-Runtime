# Fase 11 — Enterprise Integrations

**Status:** concluída (2026-09). 335 testes · `ruff` limpo · migração `010_integrations.sql`.

O Runtime já governava trabalho, já conversava com gente (Fase 10) e já provava
qualidade (Fase 8). Faltava o resto da empresa: **os sistemas que guardam os
dados de verdade**. Eles não entram como extensão do agente — entram como
fronteira.

> Conector é declarado, credencial é do cofre, política decide e cada chamada
> fica registrada. O agente não inventa URL: ele pede um nome.

---

## 1. O caminho de uma chamada

```
agente/CLI/API
   │  integration.call(CRM, GET /clientes)
   ▼
ConnectorService
   1. conector existe?            → senão, recusa
   2. está habilitado?            → senão, recusa
   3. host na lista branca?       → senão, recusa
   4. método permitido?           → senão, recusa
   5. somente leitura + escrita?  → recusa antes da política
   6. POLICY ENGINE               → allow | require_approval | deny
   7. transporte (REST/GraphQL/SQL)
   8. registro: latência, status, custo, decisão, ator + trilha
```

Toda recusa é **fato registrado** (`integration_calls` com `ok=false` e motivo,
evento `integration.denied`): o que o Runtime não autorizou, ele não faz — e
fica escrito que alguém tentou.

## 2. O que a política julga: a operação, não o verbo HTTP

Um `POST` de GraphQL pode ser só leitura (`query`); um `select` de SQL não é
menos leitura por isso. Antes de pedir autorização, o Runtime **normaliza** o
pedido em uma operação:

| tipo | operação normalizada | leitura? |
| --- | --- | --- |
| REST | verbo HTTP (`GET`, `POST`, …) | `GET/HEAD/OPTIONS` |
| GraphQL | `QUERY` ou `MUTATION` (lido do texto) | `QUERY` |
| SQL | primeiro verbo do statement (`SELECT`, `DELETE`, …) | `SELECT/WITH/PRAGMA/EXPLAIN` |

Regras padrão (`policies/defaults.py`):

| regra | condição | decisão |
| --- | --- | --- |
| `integration-write-production` | `write == True` em produção | aprovação (operador) |
| `integration-write` | `write == True` | aprovação (operador) |
| `integration-read` | leitura | permitido |
| `integration-receive` | evento de entrada | permitido (registra, não executa) |

No plano de uma task, o Policy Engine do `AgentEngine` já autorizou o passo; o
conector registra `decisão = "integration.call (autorizado no plano)"` em vez de
pedir aprovação duas vezes para o mesmo pedido.

## 3. Declaração: `integrations/*.yaml`

```yaml
integrations:
  - id: CRM
    name: CRM (clientes)
    type: rest                       # rest | graphql | sql | webhook
    enabled: false                   # nasce desligado: habilitar é ato consciente
    base_url: https://api.exemplo.com/v1
    auth:
      scheme: bearer                 # none | bearer | header | basic
      secret: vault:CRM_TOKEN        # referência, nunca o segredo
    allowed_hosts: [api.exemplo.com] # lista branca: vazia = nada sai
    allowed_methods: [GET]           # vazio = só leitura
    read_only: true
    timeout: 20
    max_response_chars: 4000
    cost_per_call: 0.002
```

Variantes do mesmo contrato:

```yaml
  - id: ERP            # graphql: query explícita, variáveis tipadas
    type: graphql
    base_url: https://erp.exemplo.com/graphql
    allowed_methods: [POST]
  - id: WAREHOUSE      # sql: só drivers declarados (padrão: sqlite)
    type: sql
    dsn: sqlite:///.egr/egr.db
    read_only: true
  - id: FORNECEDOR     # webhook de ENTRADA
    type: webhook
    inbound:
      enabled: true
      secret: vault:FORNECEDOR_WEBHOOK_SECRET
      event_type: integration.fornecedor
```

Credencial é resolvida por `runtime.resolve_secret()`: `vault:NOME` vem do cofre
cifrado; `VARIAVEL` vem do ambiente. O YAML nunca carrega o valor.

`egr integration sync` **reafirma o YAML**: se um conector foi habilitado só por
CLI/API (ato de runtime), o próximo sync volta ao que está declarado. Habilitar
de forma durável é editar o arquivo — o governo é versionado, não é memória de
processo.

## 4. Entrada: webhook assinado e idempotente

```
POST /v1/integrations/{id}/events   (assinatura HMAC em X-EGR-Signature)
  1. conector e webhook habilitados?      → 401 se não
  2. assinatura confere e está na janela? → 401 se não (tolerância: 300 s)
  3. id do evento já visto?               → status "duplicate", não reprocessa
  4. registra evento + publica no barramento
  5. só um gatilho de workflow declarado transforma isso em trabalho
```

Assinatura: `t=<timestamp>,v1=hmac_sha256(secret, "<t>:<body>")` — o corpo é
serializado com `sort_keys`, então a ordem das chaves não importa; o timestamp
impede replay. Entrada **não executa ferramenta**: vira evento auditado e, se
houver workflow com `trigger.event` casando, uma execução governada.

## 5. Ferramentas novas

| ferramenta | risco | faz |
| --- | --- | --- |
| `integration.call` | médio (rede, efeitos) | chama um conector declarado; o agente passa `integration`, `path`/`query`, `body`, `variables` — nunca URL nem token |
| `integration.list` | baixo | lista conectores (sem credencial) |

Chamadas de escrita feitas por agente passam pelo mesmo caminho: o plano da task
é autorizado pelo Policy Engine e, quando a regra exige, a task **para em
aprovação** antes de tocar o sistema alheio.

## 6. Configuração

```yaml
integrations:
  enabled: true
  max_response_chars: 4000       # corpo alheio não é memória do Runtime
  default_timeout: 20
  redact: true                   # segredo não entra na trilha
  allow_sql_drivers: [sqlite]    # driver novo é declaração, não descoberta
```

## 7. Superfície

### CLI

```bash
egr integration list                     # conectores e o que cada um pode
egr integration show CRM                 # detalhe + últimas chamadas
egr integration sync                     # integrations/*.yaml -> registro
egr integration enable WAREHOUSE --by human:vitor
egr integration enable WAREHOUSE --disable
egr integration test CRM                 # GET / · select 1 · introspecção
egr integration call CRM /clientes       # chamada governada
egr integration call WAREHOUSE --query "select count(*) as total from tasks"
egr integration call CRM /pedidos -X POST -d '{"valor": 10}' --dry-run
egr integration calls                    # destino, decisão, latência, custo, ator
egr integration events                   # o que chegou de fora
```

### API

| rota | faz |
| --- | --- |
| `GET /v1/integrations` | status (conectores, chamadas, eventos) |
| `GET /v1/integrations/{id}` | declaração (sem credencial) |
| `POST /v1/integrations/{id}/enable?disable=false` | habilitar/desabilitar |
| `POST /v1/integrations/{id}/call` | chamada governada |
| `POST /v1/integrations/{id}/test` | teste sem efeito colateral |
| `GET /v1/integrations/calls` | chamadas registradas |
| `GET /v1/integrations/events` | eventos de entrada |
| `POST /v1/integrations/{id}/events` | webhook assinado (entrada) |

### Runtime

```python
runtime.connectors                     # ConnectorService
runtime.integrations_status()          # usado por status/health/doctor
runtime.connectors.call("CRM", path="/clientes")
runtime.connectors.receive("FORNECEDOR", payload, headers=...)
runtime.integrations_repository        # declarações
runtime.integration_calls              # chamadas medidas
runtime.integration_events             # eventos idempotentes
```

## 8. Trilha auditada

| evento | quando |
| --- | --- |
| `integration.called` | chamada executada (ok ou falha de transporte) |
| `integration.denied` | recusa (desabilitado, host, método, leitura, política) |
| `integration.tested` | reservado para testes declarados |
| `integration.event_received` | evento de entrada aceito e registrado |
| `integration.event_rejected` | assinatura inválida, replay ou conector desligado |

Tabelas: `integrations`, `integration_calls`, `integration_events` (índice único
em `(integration, external_id)` — a idempotência é do banco, não da memória).

## 9. Saúde (`egr doctor`)

- `integrations`: conectores declarados, chamadas e eventos.
- `integracao:<id>`: **vermelho** quando um conector REST/GraphQL está habilitado
  sem lista branca de hosts. SQL não entra nessa checagem (a fronteira é o
  driver declarado).

## 10. Verificação

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest -q                     # 335 passed
bash scripts/demo.sh                    # 17 passos, inclui Fase 11
egr integration sync && egr integration list
```

Ao vivo (workspace de exemplo):

```bash
egr integration enable WAREHOUSE --by human:vitor
egr integration call WAREHOUSE --query "select count(*) as total from tasks"
egr integration call WAREHOUSE --query "delete from tasks"   # recusada: somente leitura
egr integration calls
```

## 11. Lacunas declaradas

| lacuna | nota |
| --- | --- |
| Conectores de catálogo (ERP/CRM/RH prontos) | Fase 11 entrega o **mecanismo** e quatro exemplos; adaptadores prontos entram com os Packs Verticais (Fase 12) |
| Anexos e mídia | Fase 10 não trata anexos; integrações de arquivo (Drive/S3) seguem sem conector declarado |
| Transação distribuída | não há compensação automática em sistema alheio: escrita externa é aprovada caso a caso |
| Fila de saída | chamadas são diretas; retry com backoff e fila durável não estão implementados |
| Paginação e rate limit do fornecedor | o conector respeita `timeout` e trunca resposta; paginação é do chamador |

## 12. Decisões

ADR-040 (conector declarado, nunca inventado) · ADR-041 (política julga a
operação) · ADR-042 (entrada é evento idempotente) — em
[`docs/DECISIONS.md`](DECISIONS.md).
