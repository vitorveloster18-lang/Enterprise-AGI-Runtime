# Arquitetura do EGR — conceito → código

Este documento mapeia a arquitetura definida para o Enterprise AGI Runtime às
implementações reais em `src/egr/`.

## Panorama

```
                    ENTERPRISE AGI RUNTIME
                              │
 ┌────────────────────────────┴────────────────────────────┐
 │ EXPERIENCE LAYER     egr/cli · egr/api (Telegram/Slack na Fase 10) │
 ├─────────────────────────────────────────────────────────┤
 │ CONTROL PLANE        egr/runtime (Runtime, TaskEngine, AgentEngine) │
 ├─────────────────────────────────────────────────────────┤
 │ ORCHESTRATION        egr/runtime/events.py (EventBus)               │
 ├─────────────────────────────────────────────────────────┤
 │ EXECUTION            egr/tools (registry + builtins)                │
 ├─────────────────────────────────────────────────────────┤
 │ INTELLIGENCE         egr/models (ModelGateway + providers)          │
 ├─────────────────────────────────────────────────────────┤
 │ MEMORY               egr/memory + storage (FTS5)                    │
 ├─────────────────────────────────────────────────────────┤
 │ GOVERNANCE           egr/policies · egr/security · egr/audit        │
 ├─────────────────────────────────────────────────────────┤
 │ DATA / INTEGRATION   egr/storage (SQLite) · Tools/Extensions       │
 ├─────────────────────────────────────────────────────────┤
 │ INFRASTRUCTURE       migrations · containers (sandbox) · OpenTelemetry (V2) │
 └─────────────────────────────────────────────────────────┘
```

## Objetos fundamentais

| Objeto | Modelo | Onde vive |
|---|---|---|
| Enterprise | `domain/enterprise.py` | `enterprises` (SQLite) |
| Environment | `domain/enums.py` (`development`/`staging`/`production`) | config + coluna em tasks/policies |
| Agent | `domain/agent.py` | `agents/*.yaml` → `agents` |
| Task | `domain/task.py` | `tasks` |
| Workflow | `domain/workflow.py` | `workflows/*.yaml` |
| Tool | `domain/tool.py` + `tools/` | registry em memória (builtins) |
| Memory | `domain/memory.py` + `memory/service.py` | `memory_records` + `memory_fts` |
| Policy | `domain/policy.py` + `policies/` | `policies/*.yaml` → `policies` |
| Model | `domain/agent.py::ModelSpec` + `models/` | config (`egr.yaml`) |
| Extension | `domain/extension.py` | Fase 3+ |
| Approval | `domain/approval.py` | `approvals` |
| Event | `domain/event.py` + `audit/ledger.py` | `events` (hash chain) |
| Artifact | `domain/artifact.py` | `artifacts/` + tabela `artifacts` |

## Princípio arquitetural (inalienável)

```
MODEL  → THINK / PROPOSE      egr/models        (nunca executa)
RUNTIME→ GOVERN               egr/runtime       (orquestra e autoriza)
TOOL   → EXECUTE              egr/tools         (fronteira com o mundo)
MEMORY → REMEMBER             egr/memory        (pertence à empresa)
POLICY → AUTHORIZE            egr/policies      (nunca depende do prompt)
HUMAN  → APPROVE CRITICAL     egr/cli/approvals (objeto de primeira classe)
```

O modelo **não recebe autoridade** porque consegue gerar código ou comandos. Cada ação
proposta passa por `PolicyEngine.evaluate()` e só então chega a um `Tool`.

## Fluxo de execução (Fase 1)

```
USER/CLI
   │
   ▼
 TASK ──► AGENT ──► (MEMORY recall + MODEL plan)
                          │
                          ▼
                    ACTION PROPOSAL ──► POLICY ENGINE
                          │                 │
              ┌───────────┼─────────────────┤
              ▼           ▼                 ▼
            ALLOW        DENY            APPROVAL ──► HUMAN ──► resume
              │                             │
              └──────────► TOOL ──► RESULT ─┘
                             │
                    ┌────────┴────────┐
                    ▼                 ▼
                 MEMORY            AUDIT (hash chain)
```

Implementado em `egr/runtime/agent_engine.py` (`AgentEngine.run`) e
`egr/runtime/task_engine.py`.

Pausa e retomada: quando uma ação exige aprovação, a task entra em
`REQUIRES_APPROVAL`, o plano e o cursor ficam persistidos em `task.context`
(`plan`, `cursor`, `pending_approval`, `pending_step`) e a execução continua
exatamente de onde parou após `egr approval approve`.

## Camadas

### Policy Engine (`egr/policies`)

- **Default deny**: se nenhuma regra casar, a decisão é `deny`.
- Regras com `action` (suporta curingas `prefix.*`), `condition`, `environments`,
  `agents`, `decision` e `required_role`.
- Condições avaliadas por um interpretador **AST restrito** (`policies/conditions.py`):
  sem `eval`, sem imports, sem atributos — apenas literais, nomes do contexto,
  comparações, booleanos e uma lista curta de funções.
- **Fail closed**: condição inválida → `deny` (e o erro é registrado).
- Políticas embutidas (`policies/defaults.py`) + políticas declaradas em
  `policies/*.yaml` (sincronizadas com `egr policy sync`).

### Model Gateway (`egr/models`)

- `ModelProvider` abstrato; providers `echo` (offline), `ollama` (local) e
  `openai_compat` (qualquer API compatível).
- Roteamento por **capacidade** e prioridade, com fallback automático.
- **Data Boundary**: antes de um payload sair → classificação → minimização →
  sanitização → política. `external_ai: forbidden` bloqueia, `restricted` sanitiza
  (CPF, CNPJ, e-mail, telefone, cartão, chave Pix).
- Cada chamada vira evento `model.called` / `model.failed` no ledger.

### Tool Runtime (`egr/tools`)

- Protocolo único (`Tool.execute(request, ctx) -> ToolResult`).
- Confinamento de paths: nenhuma ferramenta de arquivo sai do workspace
  (`core/paths.py::resolve_within`).
- `python.execute`: subprocesso com ambiente filtrado (sem variáveis de segredo),
  `cwd` no sandbox, timeout rígido e coleta de artefatos produzidos.
- `database.query`: conexão **read-only** e somente `SELECT/WITH`.
- `http.request`: única porta de saída; respeita `allow_network_tools` e a política.

### Memory (`egr/memory`)

- Namespaces por agente; tipos `knowledge`, `operational`, `episodic`.
- Busca full-text (SQLite FTS5, `remove_diacritics`) com fallback para `LIKE`.
- Toda escrita e toda recuperação relevante geram eventos de auditoria.
- `semantic` (embeddings) entra na Fase 5 completa.

### Audit (`egr/audit`)

- Ledger append-only: `hash = sha256(prev_hash | canonical_json(payload))`.
- `egr audit verify` detecta qualquer adulteração histórica.
- Eventos cobrem: task, agente, plano, ação proposta, decisão de política,
  aprovação, decisão humana, ferramenta, modelo, memória, artefato e fronteira de dados.

### Persistência (`egr/storage`)

- SQLite (WAL) com migrações versionadas em `migrations/*.sql`.
- Repositórios por objeto — trocar por PostgreSQL (Fase plataforma) exige
  reimplementar apenas `storage/repositories.py` e o dialeto das migrações.

## Deployment

LOCAL · OFFLINE · HYBRID · PRIVATE CLOUD · VPS · ON-PREMISE.

V1 roda 100% local a partir de um diretório de workspace versionado:

```
workspace/
├── egr.yaml          # enterprise, ambiente, modelos, segurança
├── agents/           # agentes (fonte da verdade)
├── policies/         # políticas (default deny)
├── workflows/        # processos
├── documents/        # dados de entrada
├── artifacts/        # saídas dos agentes
├── logs/             # logs estruturados (JSONL)
└── .egr/             # estado: banco + sandbox
```
