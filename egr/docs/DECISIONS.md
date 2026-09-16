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

**Dívida aceita:** isolamento real (gVisor/contêiner com seccomp, rede desligada) é
trabalho da Fase 3/Infra. Até lá, o sandbox é um isolamento de processo + política,
**não** uma fronteira de segurança forte.
