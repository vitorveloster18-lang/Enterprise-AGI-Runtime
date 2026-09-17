# Enterprise AGI Runtime (EGR)

> _The model thinks. The Runtime governs. Tools execute. Memory belongs to the enterprise._

O EGR é a infraestrutura que permite que **qualquer modelo de IA execute trabalho real
dentro de uma empresa** — com memória, ferramentas, processos, permissões, segurança,
auditoria e supervisão humana.

O modelo é substituível. O Runtime é permanente.

**Status atual:** `v0.1.0` · **Fases 0 a 10 implementadas** (V1: Foundation → Orchestration; V2: Development Environment, Evaluation, Production Governance e Remote Control) · Python-first.

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
| Testes | `tests/` | 278 testes (política, ferramentas, fluxo de task, auditoria, memória, gateway, custo/orçamento, sandbox, git/e-mail/browser/MCP, identidade/RBAC, cofre, chaves, memória semântica/híbrida, orquestração DAG/cron/webhook, propostas/AST/prova em sandbox, avaliação/métricas/regressão, release/gates/versão/rollback, canais/pareamento/ritmo/redação) |

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

## 5.2 Segurança e identidade (Fase 4)

```bash
# identidade verificável
egr identity add vitor --roles approver
egr identity token vitor --ttl-days 30
egr identity whoami --by egr_tkn_..._<id>.<segredo>

# cofre de segredos (cifrado em repouso)
egr key init                                        # chave mestra 0600
printf 'sk-...' | egr secret set openai --provider openai --stdin
egr secret list                                     # nunca mostra o valor
egr key rotate                                      # recifra todo o cofre

# postura
egr security status
egr security roles --role approver
```

| Papel | Concede |
|---|---|
| `viewer` | leitura de task/política/agente/auditoria |
| `operator` | executa trabalho (`task.submit`, `tools.execute`, `memory.write`) |
| `approver` | **`approval.decide`** — decide o crítico |
| `auditor` | leitura ampla de auditoria |
| `security_admin` | segredos, chaves, identidades |
| `admin` | todas (satisfaz qualquer `required_role`) |

Com `security.identity_required: true`, aprovar exige credencial válida, permissão
`approval.decide` e papel compatível — e agentes nunca aprovam o próprio trabalho.
Sem isso, a decisão acontece mas a auditoria fica marcada com
`identity_verified: false`.

```yaml
models:
  providers:
    - name: cloud
      type: openai_compat
      api_key_env: vault:openai      # credencial sai do cofre, não do YAML
```

## 5.3 Memória (Fase 5)

```bash
egr memory search "liberar pagamento sem aprovação" -n finance --explain
egr memory write "Limite de aprovação automática: R$ 5.000,00" -k knowledge -n finance
egr memory consolidate            # relatório de near-duplicatas
egr memory consolidate --apply    # arquiva a cópia menos saliente
egr memory reindex                # reconstrói os vetores
egr memory stats                  # tipos, namespaces, vetores, saliência
```

| Tipo | Guarda |
|---|---|
| `knowledge` | regras, políticas e fatos do negócio |
| `operational` | como o trabalho foi feito (saída de ferramentas) |
| `episodic` | o que aconteceu em cada task |
| `semantic` | síntese destilada, não o episódio bruto |

A busca é **híbrida**: BM25 (léxico, exato) + cosseno (semântico, tolerante a
variação), fundidos por RRF. O embedding é **local e determinístico**
(feature hashing + stemmer PT-BR + stopwords) — sem serviço externo, sem
download, sem dado saindo da máquina.

Ciclo de vida: `importância × reforço (uso) × decaimento (meia-vida de 30
dias)`. Memória usada fica mais forte; duplicata é arquivada, nunca apagada em
silêncio; `--prune --apply` é o único caminho para remover de verdade.

## 5.4 Orquestração (Fase 6)

```bash
egr workflow validate                      # DAG, dependências, condições, cron
egr workflow run invoice-processing        # cada passo vira uma task auditada
egr workflow runs | inspect <run> | resume <run> | cancel <run>
egr workflow schedule                      # cron: vencidos + próximos
egr workflow tick                          # idempotente (feito para o cron do SO)
egr workflow triggers                      # eventos ligados a workflows
```

```yaml
steps:
  - id: s1
    agent: document-agent
    objective: Extrair os dados da nota fiscal
    outputs: {fornecedor: "{{task.answer}}"}
  - id: s2
    depends_on: [s1]
    max_attempts: 2                        # retry auditado
    on_error: compensate                   # fail | continue | compensate
    compensate_with: desfazer
    condition: "inputs['origem'] == 'upload'"
```

Dependência não concluída propaga `skipped` (nunca executa no escuro); falha sem
tolerância aborta o run; falha tolerada termina como `partial`; aprovação pausa
em `waiting` e `resume` continua. Gatilhos: `event` (prefixo `invoice.*`),
`cron` e webhook (`POST /v1/webhooks/{id}`, com identidade quando exigida).

## 5.5 Development Environment (Fase 7)

Agentes e humanos criam agents, tools, workflows e policies — **sempre como
proposta**:

```bash
egr dev new tool exemplo.linhas        # rascunho (não escreve no workspace)
egr dev validate <id>                  # verificação estática (AST, governo)
egr dev test <id> --args '{"a": 41}'   # prova isolada, dry-run, com teto de tempo
egr dev diff <id>                      # o que muda
egr dev approve <id> --by vitor        # ato humano
egr dev apply <id>                     # grava, faz backup e recarrega
```

Um agente chega até `dev.propose`. Não existe `dev.apply` para agentes: promoção
sem humano não é uma ferramenta que faltou — é um ato que pertence a pessoas.
Código proposto é lido (imports, `eval`/`subprocess`, contrato `Tool`,
namespace) e provado em diretório descartável antes de alguém assinar.
Agente não concede a outro mais do que ele mesmo tem (escalada por procuração
fechada).

## 5.6 Evaluation (Fase 8)

```bash
egr eval smoke tool venda.preco      # suíte mínima gerada da declaração
egr eval run venda.preco             # casos, métricas, limites e veredito
egr eval runs | show <run>           # histórico medido
egr eval baseline <run>              # promove uma execução a referência
egr eval security agent finance-agent
```

```yaml
cases:
  - id: com-desconto
    args: {quantidade: 2, unitario: 50, desconto: 10}
    expect_ok: true
    expect: ["output['total'] == 90.0"]
thresholds:
  min_pass_rate: 1.0
  max_p95_duration_ms: 3000
  max_regressions: 0
```

Métricas (acerto, custo, p95) comparadas contra uma **baseline explícita**:
caso que passava e passou a falhar é regressão declarada, não lembrança.

## 5.7 Production Governance (Fase 9)

```bash
egr eval smoke workflow invoice-processing         # evidência primeiro
egr release create workflow:invoice-processing --to staging
egr release submit <id> && egr release approve <id> --by vitor --token egr_...
egr release deploy <id> --by vitor                 # só aprovados são aplicados
egr release versions workflow invoice-processing   # snapshots (o que pode voltar)
egr release rollback <id> --by vitor               # restaura e devolve o ambiente
```

`development → staging → production`, um degrau por vez: os gates conferem
escada, staging já aplicado, existência do artefato, **avaliação aprovada da
versão atual** e varredura de segurança sem achado crítico. Produção exige
`approver`; sem identidade verificada a API responde 401, sem permissão 403.
Versão é snapshot do conteúdo (`sha256[:16]`): rollback restaura — não reconstrói.

## 5.8 Remote Control (Fase 10)

```bash
egr gateway status | channels | bindings            # quem fala com o Runtime
egr gateway pair web ana --code 4D9D25 --role operator
egr gateway send web ana "resuma os documentos"      # vira task governada
egr gateway console                                  # conversa pelo terminal
egr gateway start --channel telegram                 # polling da Bot API
```

Telegram, Slack, Web (`/chat`) e terminal entram pelo mesmo `GatewayService`:
**pareamento obrigatório** (ninguém fala só porque achou o canal), remetente
vira `Principal` com papéis, `/run` e texto livre viram task com
`created_by` rastreável, e ritmo/redação valem para todos os canais.
Achado crítico de segurança reprova a execução **mesmo com todos os casos
passando** — prova funcional não compra imunidade de governo.

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
- [`docs/PHASE4_SECURITY_POLICY.md`](docs/PHASE4_SECURITY_POLICY.md) — Fase 4 (identidade, RBAC, cofre, chaves)
- [`docs/PHASE5_MEMORY_SYSTEM.md`](docs/PHASE5_MEMORY_SYSTEM.md) — Fase 5 (memória semântica, recuperação híbrida, ciclo de vida)
- [`docs/PHASE6_ORCHESTRATION.md`](docs/PHASE6_ORCHESTRATION.md) — Fase 6 (DAG, retry, compensação, agenda, webhooks)
- [`docs/PHASE7_DEV_ENVIRONMENT.md`](docs/PHASE7_DEV_ENVIRONMENT.md) — Fase 7 (proposta verificada, prova em sandbox, aprovação humana)
- [`docs/PHASE8_EVALUATION.md`](docs/PHASE8_EVALUATION.md) — Fase 8 (suítes, métricas, baseline, regressão, segurança)
- [`docs/PHASE9_GOVERNANCE.md`](docs/PHASE9_GOVERNANCE.md) — Fase 9 (release, gates de promoção, versão por snapshot, rollback)
- [`docs/PHASE10_REMOTE_CONTROL.md`](docs/PHASE10_REMOTE_CONTROL.md) — Fase 10 (gateway de canais, pareamento, comandos)
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — Fases 0–12 e critérios de saída
- [`examples/acme-workspace`](examples/acme-workspace) — workspace de exemplo (vertical contábil)

## 9. Licença

Proprietário.
