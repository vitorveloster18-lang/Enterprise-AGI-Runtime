# Enterprise AGI Runtime (EGR)

> _The model thinks. The Runtime governs. Tools execute. Memory belongs to the enterprise._

O EGR é a infraestrutura que permite que **qualquer modelo de IA execute trabalho real
dentro de uma empresa** — com memória, ferramentas, processos, permissões, segurança,
auditoria e supervisão humana.

O modelo é substituível. O Runtime é permanente.

**Status atual:** `v0.1.0` · **Fases 0 a 3 implementadas** (Foundation + Runtime Core + Model Gateway + Tool Runtime) · Python-first.

---

## 1. Instalação

```bash
make setup                    # cria .venv e instala em modo editável
source .venv/bin/activate     # ou use .venv/bin/egr direto
egr version
```

Requisitos: Python 3.11+, nenhuma infraestrutura externa (SQLite local, roda offline).

## 2. Primeiro milestone (60 segundos)

```bash
egr init ./minha-empresa --enterprise acme --name "ACME Contabilidade"
cd ./minha-empresa

egr status                    # estado do runtime
egr doctor                    # saúde (infra, políticas, modelos, auditoria)

# o milestone da V1 Alpha: uma task autônoma local
egr task "Analise os documentos desta pasta e produza um relatório."

egr task list
egr task inspect <task_id> --events
egr audit verify              # integridade da cadeia de auditoria
egr serve                     # console web + API local (http://localhost:8000)
```

Nenhuma chave de API é necessária: o provider `echo` é determinístico e offline, o que
permite exercitar **todo o caminho de governança** (planejamento → política → ferramenta →
artefato → memória → auditoria) sem nenhum modelo externo.

## 3. Adicionando um modelo real

Edite `egr.yaml` (nunca coloque a chave no arquivo — use `api_key_env`):

```yaml
models:
  providers:
    - name: local
      type: ollama                      # modelo local: dados não saem da máquina
      base_url: http://localhost:11434
      model: llama3.1
      capabilities: [reasoning, chat]
      priority: 100

    - name: cloud
      type: openai_compat               # OpenAI, Groq, OpenRouter, vLLM, LM Studio...
      base_url: https://api.openai.com/v1
      api_key_env: OPENAI_API_KEY
      model: gpt-4o-mini
      external: true                    # cruza a fronteira da empresa
      capabilities: [reasoning, chat]
      priority: 10
```

```bash
export OPENAI_API_KEY=...
egr model health
egr model test --prompt "Resuma este processo em uma frase"
```

O agente pede uma **capacidade** (`reasoning`, `fast`, `vision`, `embedding`), nunca um
fornecedor. O Model Gateway escolhe por capacidade, custo, latência, privacidade,
política e disponibilidade — e cruza a fronteira externa somente com classificação,
minimização e sanitização de dados.

## 4. Arquitetura (implementada)

```
                        CLI · API · (Telegram/Slack na Fase 10)
                                     │
   ┌─────────────────────────────────┴─────────────────────────────────┐
   │                        RUNTIME (egr/runtime)                      │
   │   TaskEngine ─► AgentEngine ─► Planner ─► Execução governada      │
   └───────┬───────────────┬───────────────┬───────────────┬───────────┘
           │               │               │               │
     POLICY ENGINE   MODEL GATEWAY    TOOL RUNTIME      MEMORY
     default deny    echo|ollama|     filesystem      knowledge
     aprovações      openai_compat    python(sandbox)  operational
     por ambiente    boundary check   http, process    episodic (FTS)
           │               │          database(ro)          │
           └───────────────┴──────────────┬─────────────────┘
                                    AUDIT LEDGER
                            append-only · hash encadeado
                                          │
                                  SQLite (local/on-prem)
```

Fluxo de uma task (Fase 1):

```
CLI → Task → Agent → (Memory + Model) → Plan → Action Proposal
    → Policy → ALLOW | DENY | APPROVAL → Tool → Result → Memory → Audit
```

## 5. O que já existe em código

| Camada | Módulo | Situação |
|---|---|---|
| Domínio | `egr/domain` | Enterprise, Environment, Agent, Task, Workflow, Tool, Memory, Policy, Model, Extension, Approval, Event, Artifact |
| Infra | `egr/storage` | SQLite, migrações versionadas, repositórios por objeto |
| Governança | `egr/policies` | Policy Engine (default deny), condições seguras (AST, sem `eval`), políticas em YAML |
| Aprovações | `egr/runtime/runtime.py` | Approval como objeto de primeira classe, retomada de task |
| Inteligência | `egr/models` | Model Gateway: roteamento por capacidade/custo/local_first, precificação, orçamento, telemetria |
| Execução | `egr/tools` | Protocolo de Tool, registry, **15 ferramentas**: filesystem, python (sandbox em contêiner), http, process, database (read-only), **git, e-mail, browser, MCP** |
| Memória | `egr/memory` | knowledge / operational / episodic, namespaces, busca full-text (FTS5) |
| Auditoria | `egr/audit` | Ledger append-only com hash encadeado + verificação |
| Segurança | `egr/security` | Redação de segredos, classificação e sanitização de dados (CPF/CNPJ/e-mail/cartão) |
| Interface | `egr/cli`, `egr/api` | CLI completo + API FastAPI + console web |
| Testes | `tests/` | 56 testes (política, ferramentas, fluxo de task, auditoria, memória, gateway, custo/orçamento, sandbox, git/e-mail/browser/MCP) |

## 6. Comandos principais

```bash
egr init | status | doctor | serve | version | logs

egr task "objetivo"                 # executa (atalho do milestone)
egr task list | inspect <id> | run | resume | cancel
egr agent list | show | create | run | sync
egr tool list | test <tool> --arg k=v --execute
egr mcp list | call mcp.<servidor>.<ferramenta> --execute
egr policy list --rules | test <ação> --arg amount=9000 | sync
egr model list | health | test | usage             # usage = custo, tokens, latência
egr memory search "texto" | write | list | stats
egr approval list | show | approve <id> | deny <id>
egr audit show --task <id> | verify | stats
egr workflow list | run <id>
egr proposal list | deploy | rollback      # Fases 7-9 (ainda não implementadas)
```

## 5.1 Ferramentas (Fase 3)

| Ferramenta | Risco | Política padrão |
|---|---|---|
| `filesystem.list/read` | low | allow |
| `filesystem.write` | medium | allow (dev/staging) · aprovação em produção |
| `python.execute` | high | allow (dev, sandbox) · aprovação em staging/produção |
| `http.request` | high | allow · aprovação em produção · bloqueada se `external_ai: forbidden` |
| `process.run` | critical | sempre aprovação |
| `database.query` | medium | allow (read-only) · aprovação em produção |
| `git.status/diff/log` | low | allow |
| `git.commit` | high | sempre aprovação |
| `email.send` | high | sempre aprovação |
| `email.read` | medium | allow · aprovação em produção |
| `browser.navigate/extract` | high | allow (dev) · aprovação nos demais |
| `mcp.*` | medium | **nenhuma regra → default deny** |

### Sandbox

```yaml
tools:
  sandbox:
    mode: auto          # auto | container | process
    image: python:3.11-alpine
    network: false      # contêiner sem rede
    memory: 512m
    cpus: '1'
```

Em modo `container`, o script roda com rede desligada, limites de recursos e o
workspace montado **read-only**. Sem Docker/Podman, cai para `process` — e o
`egr doctor` avisa que o isolamento é fraco (não finge que está tudo bem).

### MCP

```yaml
mcp:
  enabled: true
  servers:
    - name: calculadora
      command: python
      args: [servers/calculadora.py]
```

```bash
egr mcp list
egr mcp call mcp.calculadora.somar --arg a=2 --arg b=3 --execute
```

Ferramentas MCP são descobertas em runtime e **não herdam permissão nenhuma**: sem
regra de política, o default deny bloqueia.

## 6.1 Custo e orçamento (Fase 2)

```yaml
models:
  routing: cost              # priority | cost | local_first
  budget:
    per_task: 0.50           # teto por task
    per_day: 5.00            # teto por dia
    on_exceeded: deny        # deny | warn
  providers:
    - name: cloud
      type: openai_compat
      api_key_env: OPENAI_API_KEY
      model: gpt-4o-mini
      external: true
      pricing:               # USD por 1M de tokens
        input_per_1m: 0.15
        output_per_1m: 0.60
```

```bash
egr task "Processar notas fiscais de agosto"
egr task inspect <task_id>      # custo, tokens e chamadas da task
egr model usage                 # gasto por provider/modelo
egr model usage --since 2026-09-01
```

Cada estouro de orçamento vira `model.budget_blocked` no ledger. É o insumo para a
comparação **ANTES x DEPOIS** que valida o valor econômico do Runtime.

## 7. Governança na prática

```bash
# desenvolvimento: execução de código é permitida no sandbox
egr policy test python.execute                          # allow

# produção: exige humano
egr policy test python.execute --env production         # require_approval (role=operator)

# default deny: nada acontece sem regra
egr policy test erp.create_invoice                      # deny

# regras de negócio em YAML (policies/finance.yaml)
egr policy sync
egr policy test payment.create --arg amount=3000        # allow
egr policy test payment.create --arg amount=9000        # require_approval (finance_manager)
```

Nada é confiado ao prompt: o modelo **propõe**, o Runtime **autoriza**, a ferramenta
**executa**, o ledger **registra**.

## 8. Documentos

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — arquitetura e mapeamento conceito → código
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — decisões arquitetuais (ADRs)
- [`docs/PHASE0_FOUNDATION.md`](docs/PHASE0_FOUNDATION.md) — Fase 0
- [`docs/PHASE1_RUNTIME_CORE.md`](docs/PHASE1_RUNTIME_CORE.md) — Fase 1
- [`docs/PHASE2_MODEL_GATEWAY.md`](docs/PHASE2_MODEL_GATEWAY.md) — Fase 2 (custo, latência, orçamento)
- [`docs/PHASE3_TOOL_RUNTIME.md`](docs/PHASE3_TOOL_RUNTIME.md) — Fase 3 (sandbox, git, e-mail, browser, MCP)
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — Fases 0–12 e critérios de saída
- [`examples/acme-workspace`](examples/acme-workspace) — workspace de exemplo (vertical contábil)

## 9. Licença

Proprietário.
