# Fase 12 — Vertical Packs

**Status:** em construção (2026-09). Parte 1 (catálogo e instalação governada)
entregue · Parte 2 (fila de saída com retry/backoff) fecha a lacuna 11b.

O Runtime já sabia governar trabalho, gente (Fase 10) e sistemas (Fase 11). O que
faltava era o óbvio incômodo: **cada empresa começa do zero**. Um pack é o começo
— nunca o governo.

> Pack é atalho para começar, não para governar: ele entra por proposta
> verificada, precisa de aprovação humana e só então escreve no workspace.

---

## 1. O caminho de um pack

```
egr pack list                       # catálogo (7 verticais)
egr pack show finance               # o que vem dentro e o que exige
egr pack check finance              # requisitos e colisões (não escreve nada)
egr pack install finance            # cria uma ChangeProposal de tipo `pack`
        │
        ├─ verificação (Fase 7)     # manifesto válido + requisitos atendidos
        ├─ aprovação humana         # `egr proposal approve`
        └─ aplicação (Fase 7)       # único escritor: escreve, recarrega, registra
```

O manifesto aplicado (`packs/<id>.yaml`) é o artefato auditado; os arquivos
materializados (`agents/`, `workflows/`, `policies/`, `integrations/`,
`evaluations/`, `documents/`) são consequência dele. Remover um pack remove o que
ele escreveu **e preserva o que foi editado depois** (checagem por impressão
digital por arquivo).

## 2. O catálogo

| pack | vertical | traz |
|---|---|---|
| `finance` | Finanças | agente de caixa, fechamento diário, política de pagamento por limiar, conector de extratos |
| `accounting` | Contabilidade | conciliação de notas, política de lançamento (sem nota = negado), ERP por SQL |
| `sales` | Comercial | qualificação de oportunidade, política de desconto (acima de 25% é negado), CRM |
| `operations` | Operações e logística | reposição de estoque, política de compra com demanda obrigatória, ERP por GraphQL |
| `hr` | Pessoas | admissão, e a política mais restritiva do catálogo: CPF e saúde não saem daqui |
| `marketing` | Marketing | produção de campanha, envio em massa sempre com aprovação |
| `support` | Atendimento | resolução de chamado, reembolso por limiar, entrada por webhook assinado |

Cada pack entrega 6 artefatos: 1 agente, 1 workflow, 1 política, 1 conector,
1 suíte de avaliação e 1 documento de política da empresa — todos **desabilitados
no que toca sistema externo** (conector nasce `enabled: false`).

O catálogo é do pacote Python (versionado com o Runtime). Um pack do time
`packs/*.yaml` **sobrepõe** o embutido de mesmo id — o workspace tem a palavra
final.

## 3. Declaração de um pack

```yaml
id: finance
name: Financeiro
version: 1.0.0
vertical: Finanças
description: Tesouraria e fluxo de caixa
requires:
  tools: [database.query, filesystem.read]   # precisa existir
  connectors: []                             # precisa estar declarado
  min_environment: development
agents:
  - id: cashflow-agent
    objective: Projetar fluxo de caixa, conciliar extratos e apontar riscos
    model: {capability: reasoning, temperature: 0.1}
    permissions: {tools: [database.query, integration.call], namespaces: [finance]}
workflows:
  - id: fechamento-caixa
    steps: [...]
policies:
  - id: tesouraria-pagamento
    rules: [...]
integrations:
  - id: BANCO
    type: rest
    enabled: false        # pack não liga sistema externo
evaluations:
  - id: finance-fechamento
    target_kind: workflow
documents:
  politica-caixa.md: |
    # Política de Caixa
```

Todo artefato precisa sobreviver ao loader do tipo correspondente (`AgentSpec`,
`Workflow`, `Policy`, `Integration`, `EvaluationSuite`): o teste
`test_catalog_packs_are_valid_artifacts` roda essa validação para os 7 packs.

## 4. Verificação (`egr pack check`)

| checagem | nível | impede instalação |
|---|---|---|
| ferramenta declarada em `requires.tools` existe | erro | sim |
| conector declarado em `requires.connectors` está declarado | erro | sim |
| agente declarado em `requires.agents` existe | erro | sim |
| ambiente mínimo do pack | erro | sim |
| pack tem ao menos um artefato | erro | sim |
| artefato com o mesmo id já existe no workspace | aviso | não (avisa que será sobrescrito) |

Reprovado → evento `pack.denied` na trilha e nenhuma proposta é criada.

## 5. Instalação é proposta (Fase 7)

`ProposalKind.PACK` entrou no ciclo da Fase 7: risco `high` (porque instala
políticas e agentes), alvo `packs/<id>.yaml`, verificação pelo validador de
propostas e aplicação pelo único escritor. Ao aplicar:

1. escreve cada artefato no diretório do tipo;
2. recarrega o que foi instalado (agentes, políticas, workflows, conectores,
   suítes);
3. grava `InstalledPack` (versão, impressão digital do conteúdo, arquivos e
   impressão de cada arquivo, proposta de origem);
4. registra `pack.installed` na trilha.

Consequência prática: **`egr pack install` nunca escreve**. Escrever é ato de
quem aprova.

## 6. Superfície

### CLI

```bash
egr pack list                        # catálogo e status de cada pack
egr pack show finance                # artefatos, arquivos e requisitos
egr pack check finance               # verificação estática
egr pack install finance --by human:vitor
egr proposal approve <id> && egr proposal apply <id>
egr pack status                      # instalados: versão, impressão, proposta
egr pack add ./meu-pack.yaml         # pack do time entra no catálogo
egr pack remove finance --yes        # preserva arquivo editado depois
```

### API

| rota | faz |
| --- | --- |
| `GET /v1/packs` | catálogo e instalados |
| `GET /v1/packs/{id}` | declaração + arquivos que a instalação escreve |
| `GET /v1/packs/{id}/check` | verificações |
| `POST /v1/packs/{id}/install` | cria a proposta (não escreve) |
| `DELETE /v1/packs/{id}` | remove o que o pack escreveu |

### Runtime

```python
runtime.packs                        # PackService
runtime.packs_status()               # catálogo + instalados (status/health)
runtime.packs_repository             # InstalledPack por workspace
runtime.dev_propose/dev_approve/dev_apply/...   # delegações do CLI `egr proposal`
```

## 7. Trilha e saúde

| evento | quando |
| --- | --- |
| `pack.installed` | aplicação concluída (com versão, impressão e quantidade de arquivos) |
| `pack.removed` | remoção (com o que foi mantido por ter sido editado) |
| `pack.denied` | tentativa de instalar pack com requisito não atendido |

`egr doctor` ganhou o check `packs` e um alerta `pack:<id>` quando a versão
instalada diverge da do catálogo (`outdated`).

## 8. Correção que a Fase 12 encontrou no caminho

O CLI `egr proposal *` (Fase 7/9) chamava `runtime.dev_propose`, `dev_verify`,
`dev_approve`, `dev_apply`, `dev_list`, `dev_show` — **nenhum deles existia**:
todos os comandos morriam com `AttributeError`, e a demonstração nunca passava
por ali (usava `egr dev *`). As delegações foram implementadas, `dev_prove` foi
definido com honestidade (prova o que **existe** no workspace; pack não tem prova
em sandbox), e há teste para cada uma.

## 9. Verificação

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest -q          # 377 testes
egr pack list && egr pack check finance
egr pack install finance --by human:vitor
egr proposal approve <id> --by human:vitor && egr proposal apply <id> --by human:vitor
egr pack status
```

## 10. Lacunas declaradas

| lacuna | nota |
| --- | --- |
| Fila de saída com retry/backoff | lacuna 11b — parte 2 desta fase |
| Atualização de pack instalado | hoje reinstalar reescreve; não há migração de conteúdo editado |
| Pack com ferramenta (código) | packs entregam declaração, não `tools/*.py`: código continua entrando por proposta com prova em sandbox |
| Dependência entre packs | não há composição (`requires.packs`); cada pack é independente |

## 11. Decisões

ADR-043 (pack entra por proposta) · ADR-044 (catálogo é código, workspace
sobrepõe) · ADR-045 (remoção preserva o que foi editado) — em
[`docs/DECISIONS.md`](DECISIONS.md).
