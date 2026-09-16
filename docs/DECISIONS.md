# Decisões arquiteturais (ADR)

Registro das decisões tomadas para a V1 — e, principalmente, do motivo.

---

## ADR-001 · V1 é 100% Python (decisão fechada)

**Status:** aceita.

**Contexto:** o objetivo da V1 é validar o Runtime e o valor econômico, não resolver
escala de infraestrutura antes de existirem usuários.

**Decisão:** todo o núcleo proprietário da V1 é Python. Sem Rust no Core, sem
arquitetura poliglota prematura.

**Consequências:** velocidade de desenvolvimento máxima; acesso ao ecossistema de IA,
documentos, OCR e automação; avaliação e experimentação baratas.
Componentes em Rust/Go só serão considerados **depois** de um gargalo real medido
(perfis, filas, sandbox de execução em escala).

---

## ADR-002 · SQLite agora, PostgreSQL depois (atrás de repositórios)

**Status:** aceita.

**Contexto:** V1 precisa rodar local, offline e on-premise, sem instalar nada.

**Decisão:** SQLite (WAL) como backend da V1, com todo o acesso a dados isolado em
`storage/repositories.py` e migrações versionadas em `migrations/*.sql`.

**Consequências:** `pip install` + `egr init` e o Runtime funciona. A troca por
PostgreSQL (necessária para multi-usuário e escala de escrita) é uma reimplementação
de repositórios + dialeto SQL, sem tocar no domínio, políticas, runtime ou CLI.

**Dívida aceita:** concorrência de escrita é serializada por lock de processo;
a V2 multi-usuário exige Postgres.

---

## ADR-003 · Default deny em tudo

**Status:** aceita.

**Contexto:** autorização não pode depender do modelo nem da boa-fé do prompt.

**Decisão:** o Policy Engine nega qualquer ação sem regra correspondente. Regras
explícitas liberam por ferramenta, ambiente, agente e condição.

**Consequências:** nenhuma ferramenta nova "funciona por padrão" — cada integração
precisa de uma política. Isso é intencional: política é produto, não burocracia.

---

## ADR-004 · Condições de política sem `eval`

**Status:** aceita.

**Contexto:** políticas como `amount >= 5000` são expressivas, mas `eval()` em cima de
conteúdo vindo de YAML/agentes é uma porta aberta.

**Decisão:** interpretador AST restrito (`policies/conditions.py`): literais, nomes do
contexto, comparações, booleanos, subscritos e uma lista curta de funções puras.
Qualquer outra construção levanta `ConditionError`, que o motor trata como **deny**.

**Consequências:** dados ausentes não autorizam (comparação com `None` é falsa), e uma
regra quebrada nunca libera uma ação.

---

## ADR-005 · Aprovação humana é objeto de primeira classe

**Status:** aceita.

**Contexto:** "o agente pediu, então executamos" não é aceitável em ambiente empresarial.

**Decisão:** `Approval` é um objeto persistido com `required_role`, `status`, decisor e
carimbo de tempo. A task pausa (`REQUIRES_APPROVAL`), guarda plano e cursor, e retoma
exatamente do ponto de parada após a decisão humana.

**Consequências:** o ciclo `APPROVAL_REQUESTED → HUMAN_DECISION → APPROVED → ACTION_EXECUTED`
é auditável e reproduzível.

---

## ADR-006 · O agente pede capacidade, não fornecedor

**Status:** aceita.

**Contexto:** GPT/Claude/Gemini/Qwen/Gemma e modelos locais vão continuar melhorando e
barateando. O Runtime precisa se beneficiar disso automaticamente.

**Decisão:** `ModelSpec.capability` (`reasoning`, `fast`, `vision`, `embedding`) é o que
o agente declara; o Model Gateway escolhe o provider por capacidade, prioridade, custo,
latência, privacidade, política e disponibilidade.

**Consequências:** trocar ou adicionar modelo é configuração (`egr.yaml`), não código.

---

## ADR-007 · Data Boundary antes de qualquer saída

**Status:** aceita.

**Contexto:** empresas não entregam seu contexto a uma API externa.

**Decisão:** antes de cruzar a fronteira: classificação → minimização → sanitização →
política. `external_ai: forbidden` bloqueia; `restricted` sanitiza (CPF, CNPJ, e-mail,
telefone, cartão, Pix); `allowed` passa direto.

**Consequências:** tarefas sensíveis podem usar modelo local e, ainda assim, usar uma
API externa para uma etapa específica — sem vazar o contexto.

---

## ADR-008 · Ferramentas e integrações nunca entram no Core

**Status:** aceita.

**Contexto:** ERP, CRM, e-mail, browser e MCP específicos não podem virar core code.

**Decisão:** tudo entra pelo Tool Protocol (ou como Extension). O Core só conhece
`ToolRequest → Policy → ToolResult`.

**Consequências:** `egr/tools/builtins/*` é a prova de conceito; conectores futuros
(ERP/CRM/contábil) são pacotes externos ao núcleo.

---

## ADR-009 · Interface é descartável, Runtime é permanente

**Status:** aceita.

**Contexto:** a primeira interface é o terminal; Telegram/Slack/Web vêm depois.

**Decisão:** CLI e API são clientes do `Runtime`. A API local expõe o mesmo núcleo que
o CLI, e gateways remotos (Fase 10) falam com essa API.

**Consequências:** trocar a interface não toca no núcleo; o console web atual é um
cliente de leitura + aprovações.

---

## ADR-010 · Provider `echo` para desenvolvimento offline

**Status:** aceita.

**Contexto:** sem modelo configurado não existe como validar o Runtime.

**Decisão:** `EchoProvider` produz um plano determinístico real
(`filesystem.list` → `python.execute` → relatório), permitindo exercitar todo o caminho
de governança offline, em testes e em demos.

**Consequências:** `egr task` funciona em máquina isolada, e a suíte de testes não
depende de rede nem de chaves.

---

## ADR-011 · Auditoria com hash encadeado desde o dia 1

**Status:** aceita.

**Contexto:** rastreabilidade empresarial não se adiciona depois.

**Decisão:** todo evento carrega `prev_hash` e `hash = sha256(prev_hash | payload)`;
`egr audit verify` detecta adulteração.

**Consequências:** o ledger é evidência, não log decorativo. Custo: uma leitura a mais
por escrita (o head da cadeia).

---

## ADR-012 · Promoção para produção nunca é automática

**Status:** aceita (implementação completa na Fase 9).

**Contexto:** "o agente criou, testou e publicou" viola o princípio de supervisão.

**Decisão:** DEV → STAGING → PROPOSAL → HUMAN APPROVAL → PRODUCTION. O agente propõe;
ele não aprova a própria promoção. Os comandos `proposal`/`deploy`/`rollback` existem na
superfície do CLI e informam explicitamente que dependem da Fase 9.

**Consequências:** nenhuma automação "esperta" contorna o humano.

---

## ADR-013 · Sandbox por subprocesso, não por mágica

**Status:** aceita (V1).

**Contexto:** execução de código gerado por modelo é o risco número 1.

**Decisão:** `python.execute` roda em subprocesso com `cwd` no sandbox, ambiente
filtrado (sem variáveis de segredo), timeout rígido e coleta de artefatos. Política
exige aprovação fora de desenvolvimento; confinamento de paths é obrigatório.

**Atualização (Fase 3):** o `SandboxRunner` agora suporta dois backends —
`container` (docker/podman: rede desligada, limites de memória/CPU/pids, workspace
read-only, tmpfs em /tmp) e `process` (subprocesso com ambiente filtrado). O modo
`auto` usa contêiner quando há runtime disponível.

**Dívida restante:** em modo `process` o isolamento **não** é fronteira de segurança
forte. Por isso o `egr doctor` reporta `sandbox:isolamento` como falha nesse modo, em
vez de fingir que está tudo bem.

---

## ADR-014 · Custo e orçamento são cidadãos de primeira classe

**Status:** aceita (Fase 2).

**Contexto:** "validar o valor econômico" é um objetivo declarado do produto — mas sem
telemetria de custo por task, o ROI vira opinião.

**Decisão:** cada chamada de modelo é precificada (`pricing` por provider), registrada
(tabela `model_calls`) e limitada (`budget.per_task` / `budget.per_day`). A task carrega
`cost`, `tokens` e `model_calls`.

**Consequências:** o Runtime pode bloquear uma execução cara antes de acontecer
(`BudgetExceeded` + evento `model.budget_blocked`), e qualquer processo automatizado
passa a ter custo comparável com horas humanas, erros e retrabalho.

---

## ADR-015 · Roteamento é estratégia declarada, não hardcoded

**Status:** aceita (Fase 2).

**Contexto:** o mesmo Runtime precisa atender "quero o melhor modelo" e "quero o mais
barato" e "nada sai da minha rede".

**Decisão:** `models.routing` aceita `priority` (ordem configurada), `cost` (menor custo
estimado) e `local_first` (locais primeiro). Todas as estratégias respeitam capacidade,
política e Data Boundary.

**Consequências:** quando os modelos ficarem melhores e mais baratos, o Runtime se
beneficia por configuração — exatamente a tese do produto.

---

## ADR-016 · Ferramentas MCP são default deny

**Status:** aceita (Fase 3).

**Contexto:** MCP permite descobrir ferramentas em runtime — ou seja, o conjunto de
capacidades do Runtime pode mudar sem deploy.

**Decisão:** ferramentas MCP entram registradas como `mcp.<servidor>.<ferramenta>` e
**não herdam permissão alguma**. Como não há regra correspondente na baseline, o default
deny as bloqueia até que uma política explicite a liberação.

**Consequências:** conectar um servidor MCP nunca amplia silenciosamente o que o agente
pode fazer. A superfície de risco cresce só quando alguém escreve a política — e isso
fica registrado no repositório.

---

## ADR-017 · Custo também é de ferramenta, não só de token

**Status:** aceita (Fase 3).

**Contexto:** medir apenas tokens subestima o custo real de uma automação (APIs pagas,
e-mail, SMS, consultas a terceiros).

**Decisão:** `ToolResult` pode declarar `cost` por chamada; o Runtime acumula em
`TaskResult.tool_cost` e expõe `total_cost = modelo + ferramentas`.

**Consequências:** o cálculo ANTES x DEPOIS fica honesto — e o orçamento (ADR-014) pode,
no futuro, cobrir ferramentas pagas com o mesmo mecanismo.

---

## ADR-018 · Cofre cifrado com a stdlib, envelope versionado

**Status:** aceita (Fase 4).

**Contexto:** o núcleo V1 do EGR é 100% Python e a stdlib não expõe AES. Segredo
de produção não pode ficar em `egr.yaml`, em variável solta ou em coluna de banco
em claro.

**Decisão:** envelope `EGR1.<nonce>.<cifra>.<tag>` com encrypt-then-MAC sobre
keystream de HMAC-SHA256, chaves derivadas por `scrypt` com salt por segredo e
verificação em tempo constante (`hmac.compare_digest`). Falha é sempre fechada
(`VaultError`). O formato é versionado desde o primeiro dia.

**Consequências:** nenhuma dependência externa e nenhum segredo em claro em
repouso. O custo é não usar AES em hardware e ser uma construção própria: por
isso o prefixo de versão existe — um backend `cryptography` (AES-GCM) pode ser
introduzido depois e a rotação (`egr key rotate`) migra o acervo sem quebrar
leitura.

---

## ADR-019 · Aprovação exige identidade verificada, não um nome

**Status:** aceita (Fase 4).

**Contexto:** "humano no loop" é inútil se `--by qualquer-coisa` basta. A
auditoria precisa responder *quem* decidiu e *com qual autoridade*.

**Decisão:** decisões passam por `Runtime._authorize_decision`, que exige
principal autenticado + permissão `approval.decide` + papel que satisfaça o
`required_role` da política, e veta agentes. Sem `identity_required`, a decisão
acontece mas é registrada com `identity_verified: false`.

**Consequências:** a garantia é auditável em vez de declarada. O padrão segue
permissivo em desenvolvimento, e o Runtime lista isso como lacuna em
`egr security status` e no `egr doctor` — ninguém confunde "funciona" com
"governado".

---

## ADR-020 · RBAC explícito, aditivo e sem herança escondida

**Status:** aceita (Fase 4).

**Contexto:** matrizes de permissão com hierarquia implícita viram pesadelo de
auditoria ("por que este papel pode aprovar?").

**Decisão:** papéis são declarados em `ROLE_PERMISSIONS` com herança explícita em
`ROLE_INHERITS`; `admin` satisfaz qualquer exigência. Permissões são checadas no
ponto de decisão (`security/rbac.py:require`), nunca na interface.

**Consequências:** a matriz cabe em uma tela (`egr security roles`) e a resposta
para "quem pode aprovar?" é uma consulta, não uma investigação.

---

## ADR-021 · Embedding local determinístico antes de qualquer provedor externo

**Status:** aceita (Fase 5).

**Contexto:** memória semântica normalmente significa chamar um serviço de
embeddings — o que envia conteúdo da empresa para fora e cria dependência de
rede. O EGR promete o oposto: a memória pertence à empresa.

**Decisão:** vetor por **feature hashing** sobre radicais (stemmer leve PT-BR),
bigramas e char 4-gramas, com `blake2b` (determinístico entre processos),
tf sublinear e normalização L2. Stopwords são removidas; char-grams entram com
peso baixo. O `model_id` é versionado e o vetor vive em tabela própria.

**Consequências:** funciona offline, custa microssegundos e não vaza dado. O
limite é semântica rasa (sem sinônimos distantes) — por isso o lado léxico
(BM25) continua na fusão e um backend melhor pode entrar depois por
reindexação, sem migração traumática.

---

## ADR-022 · Recuperação híbrida com RRF, não soma de scores

**Status:** aceita (Fase 5).

**Contexto:** BM25 e cosseno produzem escalas incomparáveis; somá-las exige
normalização frágil e quebra quando um dos lados não retorna nada.

**Decisão:** fundir por **Reciprocal Rank Fusion** ponderado
(`w_fts/(60+rank_fts) + w_sem/(60+rank_sem)`), com pesos configuráveis em
`memory.*` e modo selecionável (`hybrid` padrão, `fts`, `semantic`).

**Consequências:** só a ordem relativa importa, então nenhuma escala precisa ser
calibrada; desligar um lado é zerar um peso. O `--explain` expõe os ranks e o
cosseno — a recuperação é inspecionável, não uma caixa preta.

---

## ADR-023 · Memória tem ciclo de vida, e esquecer é um ato explícito

**Status:** aceita (Fase 5).

**Contexto:** memória que só acumula degrada a recuperação e vira risco
(conteúdo obsoleto sendo relembrado como verdade).

**Decisão:** saliência = importância × reforço (uso) × decaimento (meia-vida).
Near-duplicatas são detectadas por cosseno e a de menor saliência é
**arquivada** (fora da busca padrão, nunca apagada). Só `--prune --apply`
remove, e só depois da retenção vencer. `consolidate` sem `--apply` é relatório.

**Consequências:** o acervo se mantém útil sem perda silenciosa de história — e
toda remoção passa pela auditoria. O custo é uma varredura O(n²) na
consolidação, limitada por `max_consolidate_scan`.

---

## ADR-024 · Execução de workflow é objeto auditado, não um laço invisível

**Status:** aceita (Fase 6).

**Contexto:** um `for` sobre passos não deixa rastro: não dá para responder
"quem disparou", "por que parou", "o que o passo 2 recebeu do passo 1".

**Decisão:** `WorkflowRun` é persistido (`workflow_runs`) com status, trigger,
inputs, contexto por passo, tentativas, custo e erro. Cada passo cria uma `Task`
com `workflow_run_id` e `step_id`.

**Consequências:** retomada, cancelamento em cascata e auditoria passam a ser
consultas, não arqueologia. O custo do run é a soma dos custos das tasks.

---

## ADR-025 · Scheduler explícito: `tick` idempotente, não daemon mágico

**Status:** aceita (Fase 6).

**Contexto:** daemons próprios de scheduler são a parte menos supervisionada de
qualquer sistema — e a que mais surpreende em produção.

**Decisão:** o motor expõe `due()`/`tick()` idempotentes (um minuto = no máximo
um disparo por workflow). Quem decide a periodicidade é o ambiente: cron do SO,
systemd timer, k8s CronJob. `egr workflow daemon` existe só para desenvolvimento.

**Consequências:** reiniciar ou sobrepor execuções não duplica trabalho, e o
operador continua no controle de quando o Runtime acorda.

---

## ADR-026 · Dependência não concluída propaga `skipped`, nunca executa no escuro

**Status:** aceita (Fase 6).

**Contexto:** com `on_error: continue`, o que acontece com os passos a jusante?
Executá-los é inventar dado; falhá-los é mentir sobre a causa.

**Decisão:** antes de rodar cada nível, o motor marca `skipped` todo passo cuja
dependência não esteja `completed` — com o motivo registrado. Falha sem
tolerância **aborta** o run e a jusante fica `pending` (nunca avaliada). Run que
termina com falhas toleradas é `partial`, não `completed`.

**Consequências:** o relatório do run distingue "não rodou porque não devia" de
"não rodou porque o run morreu" — distinção essencial quando alguém pergunta
por que um processo não produziu resultado.
