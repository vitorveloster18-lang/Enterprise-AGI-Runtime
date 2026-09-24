# Fase 2 — Model Gateway ✅

**Objetivo:** o agente nunca fala com um fornecedor; o Gateway roteia por capacidade,
custo, latência, privacidade, política e disponibilidade — e o Runtime paga a conta,
com orçamento.

## O que foi implementado

| Item | Situação | Onde |
|---|---|---|
| Contrato `ModelProvider` | ✅ | `models/gateway.py` |
| Providers `echo` (offline), `ollama` (local), `openai_compat` (externo) | ✅ | `models/providers/` |
| Roteamento por capacidade e prioridade | ✅ | `ModelGateway.candidates()` |
| Estratégias `priority` · `cost` · `local_first` | ✅ | `models.routing` no `egr.yaml` |
| Precificação por provider (USD/1M tokens) | ✅ | `PricingConfig` + `estimate_cost()` |
| Telemetria de chamadas (custo, tokens, latência, status) | ✅ | tabela `model_calls` + `ModelUsageRepository` |
| Orçamento por task e por dia | ✅ | `BudgetConfig` + `BudgetExceeded` |
| Evento `model.budget_blocked` no ledger | ✅ | `EventType.MODEL_BUDGET_BLOCKED` |
| Custo na task (`result.cost`, `tokens`, `model_calls`) | ✅ | `TaskResult` |
| CLI `egr model usage` | ✅ | `cli/commands/models.py` |
| API `/v1/usage` + card de custo no console | ✅ | `api/server.py` |

## Como configurar

```yaml
models:
  routing: cost              # priority | cost | local_first
  budget:
    currency: USD
    per_task: 0.50           # teto por task
    per_day: 5.00            # teto por dia
    on_exceeded: deny        # deny | warn
  providers:
    - name: local
      type: ollama
      base_url: http://localhost:11434
      model: llama3.1
      capabilities: [reasoning, chat]
      priority: 100          # custo zero: sempre vence em `cost`

    - name: cloud
      type: openai_compat
      base_url: https://api.openai.com/v1
      api_key_env: OPENAI_API_KEY
      model: gpt-4o-mini
      external: true
      priority: 10
      pricing:
        input_per_1m: 0.15
        output_per_1m: 0.60
```

## Estratégias de roteamento

| Estratégia | Critério | Quando usar |
|---|---|---|
| `priority` | ordem configurada (padrão) | quando você sabe exatamente o que quer |
| `cost` | menor custo estimado por 1k tokens, desempate por prioridade | quando o volume importa |
| `local_first` | provedores locais primeiro; externos só se necessário | quando privacidade é o critério |

Todas respeitam `capabilities`, `external_ai` e o Data Boundary: um provider externo
nunca é chamado se a política proibir, e o payload é sanitizado em `restricted`.

## Orçamento

O orçamento é verificado **antes de cada chamada**, com base no gasto já registrado:

- `per_task` — teto por task (escopo `task`)
- `per_day` — teto por dia corrido (escopo `day`)
- `on_exceeded: deny` — bloqueia (padrão) e levanta `BudgetExceeded`
- `on_exceeded: warn` — registra o evento e continua

Todo estouro vira `model.budget_blocked` no ledger, com `scope`, `limit`, `spent` e a
ação tomada. `egr doctor` mostra o consumo do dia quando há `per_day` configurado.

## Custo como insumo do valor econômico

A Fase 2 é o que torna mensurável a tese da seção "Como validar o valor econômico":

```bash
egr task "Processar as notas fiscais de agosto"
egr task inspect <task_id>       # custo, tokens, chamadas, passos
egr model usage                  # gasto do workspace por provider/modelo
egr model usage --task <id>      # gasto de uma task específica
egr model usage --since 2026-09-01
```

Cada task carrega `cost`, `tokens` e `model_calls`. Comparado com horas humanas,
erros e retrabalho do processo (Fase 8), isso fecha o cálculo **ANTES x DEPOIS**.

## Telemetria

Tabela `model_calls` (migração `002_model_usage.sql`):

| coluna | significado |
|---|---|
| `provider`, `model`, `capability` | quem atendeu e com qual capacidade |
| `external` | cruzou a fronteira da empresa? |
| `latency_ms` | latência medida pelo Runtime |
| `input_tokens`, `output_tokens` | normalizados por provider (ollama → openai) |
| `cost` | calculado com o `pricing` configurado |
| `status`, `error` | `ok` ou `error` (falhas também custam visibilidade) |

## Próximo passo (Fase 3)

Tool Runtime completo: sandbox forte (contêiner/seccomp, rede desligada) e ferramentas
de browser, git e e-mail. O custo de ferramentas externas (APIs pagas) também passará
pelo mesmo orçamento.
