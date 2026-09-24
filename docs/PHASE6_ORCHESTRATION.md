# Fase 6 — Orchestration ✅

**Objetivo do documento original:** orquestração de workflows, tasks, agents,
subprocessos e coordenação entre agentes.

| Item | Situação | Onde |
|---|---|---|
| **Execução como objeto** (`WorkflowRun`) | ✅ | `domain/run.py` + `workflow_runs` |
| **DAG** (dependências reais, não ordem do arquivo) | ✅ | `WorkflowEngine.levels` (Kahn) |
| **Retry** com tentativas auditadas | ✅ | `WorkflowStep.max_attempts` |
| **Condição** por passo (avaliador seguro) | ✅ | `policies/conditions.py` |
| **Compensação** declarada | ✅ | `on_error: compensate` |
| **Tolerância a falha** (`continue`) e `partial` | ✅ | `Workflow.on_error` |
| **Paralelismo** por nível do DAG | ✅ | `parallel` + `max_parallel` |
| **Retomada** após aprovação | ✅ | `egr workflow resume` |
| **Cancelamento** em cascata | ✅ | `egr workflow cancel` |
| **Scheduler** (cron, idempotente) | ✅ | `egr workflow tick/schedule/daemon` |
| **Gatilho por evento** | ✅ | `runtime/triggers.py` |
| **Webhook** (com identidade quando exigida) | ✅ | `POST /v1/webhooks/{id}` |
| Sub-tasks e coordenação multi-agente | 🟡 | `Task.parent_id` + passos com agentes distintos; falta handoff negociado (Fase 11) |

## 1. Cada passo é uma task

Orquestração **não** é atalho para pular governança: o passo vira `Task`, passa
por agente → política → ferramenta → auditoria, e carrega `workflow_run_id` e
`step_id`. O que o workflow acrescenta é ordem, condição, retry e compensação.

```bash
egr workflow validate                 # DAG, dependências, condições e cron
egr workflow run invoice-processing   # executa e mostra o run
egr workflow runs                     # histórico
egr workflow inspect <run_id>         # passo a passo
egr workflow resume <run_id>          # retoma (ex.: após aprovação)
egr workflow cancel <run_id>          # cancela run + tasks abertas
```

## 2. DAG, condição, retry e compensação

```yaml
steps:
  - id: s1
    agent: document-agent
    objective: Extrair e classificar a nota fiscal
    outputs:
      fornecedor: "{{task.answer}}"
  - id: s2
    agent: finance-agent
    depends_on: [s1]
    max_attempts: 2                 # cada tentativa fica em `attempts`
    on_error: compensate            # fail | continue | compensate
    compensate_with: desfazer
    objective: Validar "{{steps.s1.outputs.fornecedor}}"
  - id: s4
    depends_on: [s3]
    condition: "inputs['origem'] == 'upload'"   # falso => skipped
```

Semântica adotada (e por quê):

| Situação | Resultado |
|---|---|
| dependência `completed` | passo executa |
| dependência `failed` tolerada ou `skipped` | passo fica **`skipped`** — nunca roda no escuro |
| falha sem tolerância | run **para**; a jusante fica `pending` (nunca avaliada) |
| falha tolerada (`continue`) | run segue e termina como **`partial`** |
| `compensate` | executa o passo de compensação e registra `workflow_compensation` |
| task em aprovação | run fica **`waiting`**; `resume` continua de onde parou |
| condição inválida/quebrada | passo é **pulado** (falha fechada, não executa) |

Interpolação disponível: `{{inputs.x}}`, `{{steps.<id>.answer}}`,
`{{steps.<id>.outputs.y}}` e `{{task.*}}` (o próprio passo).

## 3. Agenda: o operador manda (ADR-025)

```bash
egr workflow schedule          # o que está vencido + próximos disparos
egr workflow tick              # roda os vencidos (idempotente, termina)
egr workflow tick --dry-run
egr workflow daemon --interval 60   # só para desenvolvimento
```

Cron de 5 campos (`minuto hora dia_mes mes dia_semana`) com `*`, `*/n`, `a-b`,
listas. **Idempotência:** já existe run do mesmo workflow com trigger `cron` no
mesmo minuto ⇒ não dispara de novo. Em produção, quem chama o `tick` é o cron do
SO, um systemd timer ou um job — não um daemon que ninguém supervisiona.

## 4. Gatilhos

```yaml
trigger:
  type: event       # manual | event | cron | webhook
  event: invoice.*  # exato ou prefixo
```

Todo evento do ledger passa pelo EventBus; ao casar, o run começa com
`inputs = {event, event_id, payload}`. **Eventos internos de um run
(`payload.workflow_run`) são ignorados** — sem isso, um trigger `task.*`
realimentaria o workflow que acabou de criar a task.

Webhook: `POST /v1/webhooks/{workflow_id}`. Com `security.identity_required`,
exige `Authorization: Bearer egr_...` de um principal com `task.submit` —
webhook não é exceção de governança.

## 5. Correções incluídas

* **skip não propagava**: passo a jusante de dependência pulada executava como
  se estivesse tudo bem. Agora o nível é avaliado antes de rodar e o que tem
  dependência não concluída vira `skipped`, com o motivo registrado.
* **`start` tolerava workflow inválido**: passos sem objetivo executavam com
  objetivo vazio. Agora qualquer problema de declaração é `ConfigError`.

## Próximo passo (Fase 7)

Development Environment: agentes criando agents, tools e workflows dentro do
sandbox — sempre como proposta, nunca como promoção automática.
