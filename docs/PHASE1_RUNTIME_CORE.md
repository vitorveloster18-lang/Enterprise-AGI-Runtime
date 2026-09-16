# Fase 1 — Runtime Core ✅

**Objetivo:** Runtime, Agent, Task, Tool, Event, State e Artifact funcionando ponta a
ponta — tudo Python, tudo local.

## Fluxo implementado

```
CLI
 ↓
Task ──► Agent ──► (Memory + Model) ──► Plan
 ↓
Action Proposal ──► Policy ──► ALLOW | DENY | APPROVAL
 ↓
Tool (sandbox) ──► Result ──► Memory ──► Artifact ──► Audit
```

## Entregáveis

| Item | Situação | Detalhes |
|---|---|---|
| Runtime | ✅ | `Runtime.load()` amarra storage, políticas, gateway, ferramentas e engines |
| Task Engine | ✅ | `create`, `submit`, `run`, `resume`, `cancel`, `list` |
| Agent Engine | ✅ | planeja, propõe, autoriza, executa, registra, finaliza |
| Planner | ✅ | prompt estruturado → JSON validado; fallback quando o modelo falha |
| Tool Runtime | ✅ | filesystem (list/read/write), python (sandbox), http, process, database (read-only) |
| Policy Engine | ✅ | default deny, regras por ambiente/agente/condição |
| Approval | ✅ | pausa a task, retoma após decisão humana |
| Event / State | ✅ | estado da task persistido (`status`, `context`, `result`) |
| Artifact | ✅ | arquivos produzidos viram artefatos versionados |
| Memory | ✅ | operational + episodic escritos automaticamente; recall antes de planejar |
| Audit | ✅ | ledger append-only com hash encadeado |
| CLI | ✅ | `task`, `agent`, `tool`, `policy`, `model`, `memory`, `approval`, `audit`, `workflow` |
| API local | ✅ | FastAPI + console web |

## Milestone da V1 Alpha

```bash
egr task "Analise os documentos desta pasta e produza um relatório."
```

Executa localmente:

1. `task.created`
2. `agent.loaded`
3. `memory.recalled` (contexto relevante)
4. `model.called` → `plan.created` (2 passos)
5. `action.proposed` → `policy.allowed` → `tool.executed` (filesystem.list)
6. `action.proposed` → `policy.allowed` → `tool.executed` (python.execute no sandbox)
7. `artifact.created` (relatorio.md) + `memory.written`
8. `task.completed`

Sem rede, sem chave de API, com auditoria verificável (`egr audit verify`).

## Governança demonstrada

| Cenário | Comando | Resultado |
|---|---|---|
| Código em desenvolvimento | `egr policy test python.execute` | `allow` |
| Código em produção | `egr policy test python.execute --env production` | `require_approval` (operator) |
| Ação sem regra | `egr policy test erp.create_invoice` | `deny` (default deny) |
| Regra de negócio | `egr policy test payment.create --arg amount=9000` | `require_approval` (finance_manager) |
| Path fora do workspace | `egr tool test filesystem.read --arg path=/etc/passwd -x` | recusado (`escapes the allowed root`) |
| SQL destrutivo | `egr tool test database.query --arg sql="delete from tasks" -x` | recusado (read-only) |

## Critério de saída

Uma task autônoma local, governada e auditada. **Atingido.**

## Próximo passo (Fase 2)

Model Gateway completo: providers locais e externos configuráveis, roteamento por
custo/latência/privacidade, cache e orçamento por task. O contrato `ModelProvider` e o
Data Boundary já existem — falta telemetria de custo e políticas de orçamento.
