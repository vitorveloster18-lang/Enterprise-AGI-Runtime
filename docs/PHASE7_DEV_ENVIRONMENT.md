# Fase 7 — Development Environment

**Status:** concluída (2026-09). 167 testes · `ruff` limpo.

Um Runtime que só executa o que foi escrito por humanos é uma ferramenta de
automação. Um Runtime que os agentes ajudam a construir é uma plataforma — desde
que eles não decidam sozinhos.

A Fase 7 é o ambiente onde o trabalho novo nasce: **agentes e humanos criam
agents, tools, workflows e policies dentro do sandbox — sempre como proposta.**

---

## 1. O ciclo

```
propor → verificar → provar (código) → aprovar → aplicar
```

| Etapa | Quem pode | O que acontece |
|---|---|---|
| `propose` | agente (`dev.propose`) ou humano | grava em `.egr/dev/` + banco. **Não escreve no workspace** |
| `validate` | Runtime (automático) | verificação estática: sintaxe, contrato, governo, escalada |
| `trial` | agente (`dev.trial`) ou humano | executa a ferramenta em diretório descartável, isolada, `dry_run` |
| `approve` | **humano** | libera a aplicação |
| `apply` | humano | grava o arquivo, faz backup do anterior e recarrega o Runtime |

Estados: `draft` → `validated` → `tested` → `approved` → `applied`
(ou `failed` / `rejected`).

```bash
egr dev new tool exemplo.linhas       # rascunho (ou --from arquivo.py)
egr dev validate <id>                 # verificações estáticas
egr dev test <id> --args '{"a": 41}'  # prova no sandbox
egr dev diff <id>                     # o que muda no workspace
egr dev approve <id> --by vitor       # ato humano
egr dev apply <id>                    # grava e recarrega
egr dev list | show <id> | tools | status
```

---

## 2. O que o Runtime lê antes de aceitar

### Verificação estática (nenhuma linha é executada)

| Verificação | O que pega |
|---|---|
| `conteúdo` / `sintaxe` | vazio, YAML inválido, Python que não compila |
| `declaração` | não casa com o modelo de domínio (`AgentSpec`, `Workflow`, `Policy`) |
| `nome` | o que está declarado é o que foi proposto |
| `grafo` | reusa o validador da Fase 6 (ciclo, dependência órfã, condição, cron) |
| `autoria` | `agent:<id>` precisa existir; `human:<ator>` precisa de ator |
| `ambiente` | proposta não nasce em produção (promoção é Fase 9) |
| `escalada` | agente não concede mais do que tem |
| `colisão` | aviso: vai substituir artefato existente |
| `integridade` | o conteúdo não mudou depois de registrado |

### Verificação de código (ferramentas)

Análise de AST, sem importar o módulo:

- **imports:** lista branca (`json`, `pathlib`, `re`, `math`, `egr`, …);
  `subprocess`, `socket`, `pickle`, `ctypes`, `importlib`, `multiprocessing`,
  `urllib.request`, `smtplib` e afins são recusados;
- **chamadas proibidas:** `eval`, `exec`, `compile`, `__import__`, `os.system`,
  `shutil.rmtree`, `pickle.loads`, `getattr` dinâmico, `open()` com caminho
  absoluto ou `..`;
- **atributos internos:** `__class__`, `__globals__`, `__subclasses__`,
  `__code__`, `__dict__`, …;
- **estrutura:** sem código solto no nível superior (nada executa na importação);
- **contrato:** uma classe `Tool` por módulo, com `spec = ToolSpec(...)` literal
  e `execute(self, request, ctx)`;
- **namespace:** todas as ferramentas do arquivo começam com o prefixo do módulo
  (`exemplo.py` declara `exemplo.*`) — uma proposta não registra ferramenta
  alheia;
- **revisão:** laço infinito (`while True` sem `break`) e `time.sleep()` viram
  aviso; arquivos acima de 400 linhas são recusados por serem irrevisáveis.

### Prova em sandbox (`dev.trial`)

O julgamento de código não é "eu li e parece bom": é rodar.

- diretório descartável (`workspace/`, `sandbox/`, `artifacts/` próprios);
- interpretador novo, ambiente sem segredos (`SENSITIVE_ENV_MARKERS`),
  `dry_run=True`, teto de tempo;
- coleta resultado, duração, arquivos criados e erro;
- usa o sandbox da Fase 3: contêiner quando há Docker/Podman, subprocesso quando
  não há (lacuna declarada em `egr doctor`).

**A prova é necessária e não suficiente.** `dry_run` é uma convenção que o autor
pode ignorar, e modo `process` não é isolamento real. Por isso nenhuma ferramenta
entra sem aprovação humana — a prova reduz a assimetria, não substitui o governo.

---

## 3. O que um agente pode e o que não pode

```yaml
# agents/runtime-agent.yaml
permissions:
  tools:
    - dev.propose      # propor
    - dev.proposals    # listar
    - dev.trial        # provar o próprio código no sandbox
    - filesystem.*
```

Não existe `dev.apply` no registry. Promoção sem humano não é uma ferramenta que
faltou implementar: é um ato que pertence a pessoas.

Política (`policies/defaults.py`): `dev.propose` e `dev.trial` são permitidos em
`development` e exigem aprovação (`operator`) fora dele. Agente sem permissão
explícita continua em *default deny*.

### Escalada por procuração está fechada

Agente propondo agente é conferido contra ele mesmo, sobre **nomes concretos do
registry** (não curingas):

```
$ origem: agent:runtime-agent, proposta concede tools: ["*"]

✗ escalada · o agente 'runtime-agent' não tem as ferramentas que esta proposta
             concede: browser.extract, browser.navigate, email.read, email.send…
✗ escalada · max_risk 'critical' acima do agente de origem ('high')
✗ escalada · namespaces fora do alcance do agente de origem: finance, rh
```

---

## 4. Aplicação, linhagem e rollback

`apply` é o único caminho que escreve no workspace:

- exige status `approved` **e** conteúdo íntegro (fingerprint);
- grava em `agents/`, `tools/`, `workflows/` ou `policies/` (destino por tipo);
- **backup** do arquivo anterior em `.egr/dev/history/<proposta>__<arquivo>`;
- carimba linhagem no próprio artefato:

```python
# egr:origin: agent:runtime-agent
# egr:proposal: prp_20260917004746_742426
# egr:applied_at: 2026-09-17T00:48:03.541561+00:00
```

- recarrega o Runtime (agentes, workflows, políticas, ferramentas).

Ferramentas do workspace são **revalidadas em cada inicialização**: arquivo
adulterado à mão é recusado com evento `dev.tool_rejected` e nunca chega ao
registry — `egr doctor` mostra a recusa.

---

## 5. Superfície

**CLI** — `egr dev status|new|list|show|validate|test|diff|approve|reject|apply|tools`
(`egr proposal dev` mostra o ambiente dentro do ciclo dev→staging→produção).

**API** — tag `development`:

```
GET    /v1/dev                                   # estado do ambiente
GET    /v1/dev/proposals                         # lista (status, kind)
POST   /v1/dev/proposals                         # propõe
GET    /v1/dev/proposals/{id}
POST   /v1/dev/proposals/{id}/validate
POST   /v1/dev/proposals/{id}/test               # sandbox
POST   /v1/dev/proposals/{id}/approve
POST   /v1/dev/proposals/{id}/reject
POST   /v1/dev/proposals/{id}/apply
```

**Ferramentas** — `dev.propose`, `dev.proposals`, `dev.trial`.

**Eventos no ledger** — `dev.proposal_created`, `dev.proposal_validated`,
`dev.proposal_failed`, `dev.proposal_tested`, `dev.proposal_approved`,
`dev.proposal_rejected`, `dev.proposal_applied`, `dev.tool_loaded`,
`dev.tool_rejected`.

**Migração** — `006_dev_environment.sql` (tabela `change_proposals` + índices).

---

## 6. Verificação

35 testes novos (`tests/test_phase7_dev_environment.py`), entre eles:

- ferramenta bem formada passa; `eval`/`subprocess` são recusados;
- namespace alheio (`admin.*` em `exemplo.py`) é recusado;
- prova roda isolada e **não vê** o workspace real;
- ferramenta em laço infinito estoura o tempo e falha a proposta;
- ferramenta exige prova antes da aprovação; `apply` exige aprovação;
- agente não escala privilégio (ferramentas, `max_risk`, namespaces);
- ferramenta aplicada **sobrevive ao reload** (carregada do disco);
- arquivo adulterado no `tools/` é recusado com evento;
- conteúdo alterado depois da aprovação perde validade;
- ciclo completo é auditado.

---

## 7. Lacunas declaradas

| Lacuna | Destino |
|---|---|
| Prova em contêiner (hoje: subprocesso quando não há Docker/Podman) | reuso do sandbox Fase 3 em modo `container` — já encaminhado, depende do ambiente |
| Qualidade do artefato (o código passa, mas é bom?) | Fase 8 — Evaluation: testes, benchmark, regressão, custo |
| Promoção dev→staging→produção e rollback com versão | Fase 9 — Production Governance |
| Prova de agentes e workflows por execução (não apenas estática) | Fase 8 |
| Coordenação entre agentes construindo o mesmo artefato | Fase 11 |

O que a Fase 7 **não** faz: não deixa o agente aplicar, não aceita produção como
ambiente de origem, não confia em `dry_run` como garantia, e não trata
verificação estática como prova de qualidade.

---

## 8. Decisões

- **ADR-027** · Mudança entra como proposta verificada, nunca como arquivo escrito
- **ADR-028** · Código proposto é lido (AST) e provado (sandbox) antes do humano
- **ADR-029** · Um agente não concede a outro mais do que ele mesmo tem
