# Enterprise AGI Runtime (EGR)

> _The model thinks. The Runtime governs. Tools execute. Memory belongs to the enterprise._

O EGR é a infraestrutura que permite que **qualquer modelo de IA execute trabalho real
dentro de uma empresa** — com memória, ferramentas, processos, permissões, segurança,
auditoria e supervisão humana.

O modelo é substituível. O Runtime é permanente.

**Status atual:** `v0.1.0` · **Fases 0 a 12 implementadas** (V1: Foundation → Orchestration; V2: Development Environment, Evaluation, Production Governance, Remote Control, Enterprise Integrations e Vertical Packs) · **Fase 13 concluída: fechamento das lacunas** (10b, 12b, 8b, 9b, 6b e 5b entregues) · Python-first.

---

## 1. Instalação

```bash
make setup                    # cria .venv e instala em modo editável
source .venv/bin/activate     # ou use .venv/bin/egr direto
egr version
```

Requisitos: Python 3.11+, nenhuma infraestrutura externa (SQLite local, roda offline).

### 1.1 Partida local (um comando, sem interface online)

Para quem roda no terminal — inclusive no Termux — existe um script que **prepara
o ambiente, testa o Runtime de verdade e abre um painel de linha de comando**.
Nada de servidor: `egr serve` nunca é chamado.

```bash
bash scripts/start.sh                 # prepara + auto-teste + menu
bash scripts/start.sh --check         # só o auto-teste (sai 0 = ok, 1 = problema)
bash scripts/start.sh --shell         # prompt livre: digite comandos sem o "egr"
bash scripts/start.sh --workspace DIR # aponta para outro workspace

bash scripts/install-alias.sh          # cria o comando E (executável ~/bin/E + alias)
source ~/.bashrc && E                 # (use `bash scripts/install-alias.sh KR` para outro nome)
```

O instalador cria as duas coisas: o executável `~/bin/E` (funciona em qualquer
shell, até dentro de script) e o alias no rc do shell (atalho de digitação).

O auto-teste não pergunta se o comando existe: ele confere migrações aplicadas,
roda o `doctor`, verifica a cadeia de auditoria (`egr audit verify`) e executa
uma task pelo caminho governado. Falhas de workspace novo (sem contêiner, sem
chave mestra, sem identidades) aparecem como esperadas — o resto é problema.

### 1.2 Interface completa no terminal

Além do menu, existe uma interface de painel único — objetivo em linguagem
natural, todos os comandos e toda a configuração, sem servidor:

```bash
egr tui                    # ou: opção 13 do menu do start.sh
```

```
  ctrl+p  paleta com todos os comandos da CLI (170), com busca
  ctrl+s  configuração do workspace: modelo, orçamento, memória, segurança,
          sandbox, ferramentas, runtime, promoção, coordenação, canais
  ctrl+m  provedores de modelo (a chave entra como nome de variável de ambiente)
  ctrl+a  aprovações pendentes — o humano decide na tela
  ctrl+d  doctor · ctrl+r status · ctrl+y auditoria · ctrl+t auto-teste
  ctrl+h  ajuda · ctrl+l limpar
  /       comando direto: /task list, /memory search "contrato"
```

Escrever um objetivo e dar Enter cria a task pelo caminho governado: política,
ferramenta e trilha — a interface nunca executa por conta própria.

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
        CLI · API · Telegram/Slack/Web (Fase 10) · Sistemas externos
                    via conectores declarados (Fase 11)
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
           │               │          integration.call      │
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
| Testes | `tests/` | 654 testes (política, ferramentas, fluxo de task, auditoria, memória, gateway, custo/orçamento, sandbox, git/e-mail/browser/MCP, identidade/RBAC, cofre, chaves, memória semântica/híbrida, multimodal e limpeza de PII, interface de terminal, escopo de memória por área, áreas para pessoas, supervisão orquestrador/revisão, pacote de evidência, orquestração DAG/cron/webhook, propostas/AST/prova em sandbox, avaliação/métricas/regressão, laboratório de qualidade e carga, release/gates/versão/rollback/assinatura e quórum, coordenação negociada e gatilhos de banco, canais/pareamento/ritmo/redação, integrações REST/GraphQL/SQL/webhook, packs verticais, fila de saída, worker da fila, atualização de pack e anexos/botões dos canais) |

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

## 5.9 Enterprise Integrations (Fase 11)

```bash
egr integration sync                     # integrations/*.yaml -> registro
egr integration list | show CRM | enable WAREHOUSE --by human:vitor
egr integration call CRM /clientes       # leitura: permitida
egr integration call CRM /pedidos -X POST -d '{"valor":10}'   # escrita: aprovação
egr integration call WAREHOUSE --query "select count(*) as total from tasks"
egr integration calls                    # destino, decisão, latência, custo, ator
egr integration events                   # o que chegou de fora
```

REST, GraphQL, SQL e webhooks de entrada entram pela mesma porta: o conector é
**declarado** (host, métodos, leitura/escrita), a credencial vive no cofre e a
política julga a **operação** — um `POST` de GraphQL pode ser só leitura, um
`select` de SQL não é menos leitura por isso. Escrita em sistema alheio exige
aprovação; recusa é fato registrado. Webhook de entrada é assinado (HMAC),
idempotente por id e **não executa nada**: só um gatilho de workflow declarado
transforma evento em trabalho.

## 5.10 Vertical Packs (Fase 12)

```bash
egr pack list | show finance | check finance        # catálogo (7 verticais)
egr pack install finance --by human:vitor           # cria a proposta
egr proposal approve <id> && egr proposal apply <id> # governo da Fase 7
egr pack status | remove finance --yes
```

Finanças, Contabilidade, Vendas, Operações, RH, Marketing e Suporte entram como
**começo**, não como privilégio: cada pack traz agente, workflow, política,
conector, suíte de avaliação e documento — e passa por verificação, proposta e
aprovação humana antes de escrever qualquer arquivo. Remover preserva o que foi
editado depois da instalação.

Fila de saída (fecha a lacuna de integrações): `egr integration enqueue|drain|jobs|cancel`
com idempotência por chave, tentativas com espera crescente (30s → 60s → 120s,
com teto) e desistência registrada — o pedido sobrevive ao 503 sem duplicar efeito.

## 5.11 Anexos e botões dos canais (Fase 13 · lacuna 10b)

```bash
egr gateway upload web demo ./entrada.txt --text "classifique este anexo"
egr gateway attachments | attachment <id>          # aceitos e recusados
egr gateway send-file web demo artifacts/relatorio.md
egr gateway interact web demo aprovar apr_123      # botão = comando

egr integration worker --interval 30               # drena a fila em background
egr pack update finance                            # plano: novo/atualizável/conflito
egr pack update finance --overwrite                # sobrescreve o editado no workspace
```

Arquivo de chat é **conteúdo**, não anexo decorativo: tipo e tamanho por lista
branca, download só depois da conferência, gravação em `artifacts/inbox/`,
impressão digital na trilha e recusa visível com motivo. Na saída, só o que
estiver nas raízes liberadas — e com teto de tamanho.

Botão não executa: ele repete um comando (`aprovar` → `/aprovar <id>`) e passa
pelo mesmo pareamento, RBAC, política e auditoria.

**Laboratório de avaliação** (lacuna 8b): a Fase 8 media o encanamento; o
laboratório mede o que faltava.

```bash
egr eval judge <suíte> --method similaridade|modelo   # nota 0..1 por caso
egr eval compare <suíte> --models echo,outro          # quem entrega mais por menos
egr eval load <suíte> --requests 50 --concurrency 5   # p50/p95/p99, req/s, custo
egr eval loads                                        # histórico das simulações
```

Qualidade tem dois caminhos (similaridade determinística e juiz de modelo), e a
degradação é sempre dita. Carga não suspende governo: cada requisição passa pela
mesma política e pelo mesmo orçamento — e o que o orçamento recusou é contado à
parte, porque estourar o teto não é lentidão.

**Promoção assinada e com quórum** (lacuna 9b): aprovar um nome não protege nada
se o conteúdo mudar antes do deploy.

```bash
egr release sign <release> --by human:vitor   # assina itens, versões e evidência
egr release verify <release>                  # sai com 1 se algo mudou
egr release approvals <release>               # quem votou e quanto falta
```

A assinatura é `HMAC-SHA256` do manifesto com a chave mestra do workspace (a
mesma do cofre): mudou o release, a assinatura cai. Produção exige **dois votos
de pessoas diferentes** (`release.min_approvals_production`), ninguém vota duas
vezes, quem criou não conta para o próprio quórum e recusa é veto. O `deploy`
confere assinatura e também o manifesto que foi aprovado — aprovar uma coisa e
aplicar outra é recusado.

**Coordenação negociada e gatilhos de banco** (lacuna 6b): quem executa deixa
de ser o primeiro da lista, e o banco passa a avisar quando muda.

```bash
egr task negotiate "conciliar lançamentos" --strategy menor_fila
egr task handoff <task> --to <agente> --reason "precisa de visão de documento"
egr db trigger-add "task falhou" --on tasks --event update \
    --when "NEW.status = 'failed'" --emit db.task_failed
egr db drain                       # o que o banco avisou vira evento
```

Cada agente elegível dá um lance (custo estimado pelo provedor que usa e tamanho
da fila) e a estratégia escolhe; quem não pode aparece com o motivo do veto.
Handoff tem motivo, limite e permissão — repassar sem parar é fugir do problema.
O gatilho de banco é um `CREATE TRIGGER` real: nada de *polling*, e o `when`
aceita só colunas da lista branca (SQL livre é recusado antes de chegar no banco).

**Memória multimodal e limpeza de PII** (lacuna 5b): o que não devia estar na
memória não chega a entrar.

```bash
egr memory scrub "cpf 123.456.789-09 e fone (51) 98888-7777"   # só confere
egr memory add-media print.png --caption "erro 500 no painel"
egr memory media                      # teto, tipos aceitos, uso de disco
```

CPF e CNPJ são validados pelos dígitos verificadores, cartão por Luhn; o que sai
vira `[cpf removido]` e o registro guarda **só o tipo e a contagem** — a trilha
nunca revela o valor. A mídia vai para `artifacts/media/` (fora do banco, tipo
conferido pelos bytes, não pela extensão) e o que fica buscável é a legenda que
alguém declarou: sem visão nem transcrição local, o Runtime não inventa legenda.

Duas lacunas da Fase 12 também fechadas aqui: o **worker da fila**
(`egr integration worker`) é o processo explícito que drena o que já venceu —
com espera crescente quando a fila está parada e parada limpa por sinal; e
`egr pack update` atualiza um pack preservando, por padrão, todo arquivo que
foi editado no workspace (o plano diz o que é novo, o que muda e onde está o
conflito).

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
- [`docs/PHASE11_INTEGRATIONS.md`](docs/PHASE11_INTEGRATIONS.md) — Fase 11 (conectores REST/GraphQL/SQL/webhook, política por operação, eventos idempotentes)
- [`docs/PHASE12_VERTICAL_PACKS.md`](docs/PHASE12_VERTICAL_PACKS.md) — Fase 12 (packs verticais por proposta e fila de saída com retry)
- [`docs/PHASE13_LACUNAS_FECHADAS.md`](docs/PHASE13_LACUNAS_FECHADAS.md) — Fase 13 (fechamento das lacunas: anexos e botões, dreno, avaliação, assinatura, coordenação, memória)
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — Fases 0–12 e critérios de saída
- [`examples/acme-workspace`](examples/acme-workspace) — workspace de exemplo (vertical contábil)

## 9. Licença

Proprietário.