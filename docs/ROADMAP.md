# Roadmap — Fases 0 a 12

Situação em 2026-09: **Fases 0 a 12 implementadas e testadas** (401 testes).

| Fase | Nome | Situação | Observação |
|---|---|---|---|
| 0 | Foundation | ✅ | monorepo Python, `egr init/status/doctor`, migrações, templates |
| 1 | Runtime Core | ✅ | Task → Agent → Plan → Policy → Tool → Memory → Audit |
| 2 | Model Gateway | ✅ | roteamento por capacidade/custo/local_first, precificação, telemetria (`model_calls`), orçamento por task e por dia, `egr model usage` |
| 3 | Tool Runtime | ✅ | filesystem, python, http, process, database, **browser, git, e-mail, MCP** + sandbox forte (contêiner) e custo por ferramenta |
| 4 | Security + Policy | ✅ | identidade verificável (token com hash), RBAC checado na decisão, cofre cifrado (`EGR1`), gestão/rotação de chaves, `egr security/identity/secret/key` |
| 5 | Memory | ✅ | knowledge/operational/episodic/**semantic** com embedding local determinístico, recuperação híbrida (BM25 + cosseno via RRF), ciclo de vida (reforço/decaimento/saliência) e consolidação com arquivamento |
| 5b | — | — | Fase 5 não cobre memória multimodal, nem limpeza automática de PII na escrita — avaliação de qualidade entra na Fase 8 |
| 6b | — | — | Fase 6 não cobre coordenação negociada entre agentes (handoff/leilão de tarefas) nem triggers de banco de dados |
| 8b | — | — | Fase 8 não mede qualidade de **modelo** (só o encanamento), nem simula carga — laboratório de avaliação |
| 9b | — | — | Fase 9 promove dentro do workspace e não tem assinatura criptográfica nem quórum de aprovação — evolução de política |
| 10b | — | — | Fase 10 atende mensagem por mensagem (sem fila), não trata anexos/mídia nem botões interativos — anexos dependem de conector de arquivos (Fase 12) |
| 11b | — | — | catálogo de ERP/CRM/RH prontos entra com os Packs Verticais (Fase 12); fila de saída com retry/backoff também foi entregue na Fase 12 |
| 6 | Orchestration | ✅ | execução como objeto auditado, DAG com condição/retry/compensação/tolerância, paralelismo por nível, retomada e cancelamento, scheduler cron idempotente, gatilho por evento e webhook |
| 7 | Development Environment | ✅ | proposta verificada (AST) + prova em sandbox + aprovação humana; agentes criam agents/tools/workflows sem nunca aplicar |
| 8 | Evaluation | ✅ | suítes com casos medidos, métricas (acerto/custo/p95), limites, baseline e regressão, varredura de segurança do artefato |
| 9 | Production Governance | ✅ | release com gates (escada, evidência, segurança), versão por snapshot e rollback; produção exige staging aplicado + papel mínimo |
| 10 | Remote Control | ✅ | gateway de canais: Telegram (polling/webhook), Slack (Events API assinada), Web (/chat) e terminal, com pareamento e RBAC |
| 11 | Enterprise Integrations | ✅ | conectores declarados (REST/GraphQL/SQL/webhook): lista branca de host/método, credencial no cofre, política por operação, custo/latência/decisão registrados, eventos de entrada assinados e idempotentes |
| 12 | Vertical Packs | ✅ | 7 packs (Finanças, Contabilidade, Vendas, Operações, RH, Marketing, Suporte) instalados por proposta aprovada + fila de saída com retry/backoff |

Legenda: ✅ concluído · 🟡 parcial (base pronta) · ⏳ não iniciado

## Sequência recomendada

```
[V1] Fases 0-6     Runtime validado executando trabalho real (0-6 feitas)
[V2] Fases 7-11    Plataforma empresarial (console, RBAC, integrações, multi-agente)
[V3] Fase 12       Empresa autônoma: o Runtime constrói e evolui automações
```

## Primeiro vertical de validação: contabilidade

O workspace de exemplo (`examples/acme-workspace`) já modela o vertical:

- `documents/` com nota fiscal, relatório mensal e política de gastos;
- `agents/document-agent.yaml` (classificação de documentos) e
  `agents/finance-agent.yaml` (operações financeiras, sem `python.execute`);
- `policies/finance.yaml` com limiar de aprovação (`amount >= 5000` → `finance_manager`);
- `workflows/invoice-processing.yaml` (documento → validação → política → execução).

Por que contabilidade: documentos + regras + processos + sistemas + tarefas repetitivas
+ exceções + necessidade de supervisão. É o laboratório ideal para provar que o Runtime
governa trabalho real.

## Métricas de valor econômico (Fase 8, mas medir desde já)

Para cada processo automatizado, registrar **antes/depois** de:

- horas consumidas · pessoas envolvidas · volume · custo por operação
- erros · retrabalho · tempo de ciclo · exceções que exigiram humano

O produto precisa demonstrar economicamente o próprio valor — não perguntar
"você compraria uma IA?".
