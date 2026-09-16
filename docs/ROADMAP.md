# Roadmap — Fases 0 a 12

Situação em 2026-09: **Fases 0 a 6 implementadas e testadas** (132 testes).

| Fase | Nome | Situação | Observação |
|---|---|---|---|
| 0 | Foundation | ✅ | monorepo Python, `egr init/status/doctor`, migrações, templates |
| 1 | Runtime Core | ✅ | Task → Agent → Plan → Policy → Tool → Memory → Audit |
| 2 | Model Gateway | ✅ | roteamento por capacidade/custo/local_first, precificação, telemetria (`model_calls`), orçamento por task e por dia, `egr model usage` |
| 3 | Tool Runtime | ✅ | filesystem, python, http, process, database, **browser, git, e-mail, MCP** + sandbox forte (contêiner) e custo por ferramenta |
| 4 | Security + Policy | ✅ | identidade verificável (token com hash), RBAC checado na decisão, cofre cifrado (`EGR1`), gestão/rotação de chaves, `egr security/identity/secret/key` |
| 5 | Memory | ✅ | knowledge/operational/episodic/**semantic** com embedding local determinístico, recuperação híbrida (BM25 + cosseno via RRF), ciclo de vida (reforço/decaimento/saliência) e consolidação com arquivamento |
| 5b | — | — | Fase 5 não cobre memória multimodal, nem limpeza automática de PII na escrita — avaliação de qualidade entra na Fase 8 |
| 6b | — | — | Fase 6 não cobre coordenação negociada entre agentes (handoff/leilão de tarefas) nem triggers de banco de dados — Fase 11 |
| 6 | Orchestration | ✅ | execução como objeto auditado, DAG com condição/retry/compensação/tolerância, paralelismo por nível, retomada e cancelamento, scheduler cron idempotente, gatilho por evento e webhook |
| 7 | Development Environment | ⏳ | agentes criando agents/tools/workflows dentro do sandbox |
| 8 | Evaluation | ⏳ | testes, simulação, benchmark, regressão, custo, latência, segurança |
| 9 | Production Governance | ⏳ | promoção dev→staging→proposta→humano→produção, versionamento e rollback |
| 10 | Remote Control | ⏳ | gateways Telegram/Slack/Web sobre a API local |
| 11 | Enterprise Integrations | ⏳ | REST, GraphQL, SQL, webhooks, MCP, e-mail, browser; depois ERP/CRM/RH |
| 12 | Vertical Packs | ⏳ | Finance, Accounting, Sales, Operations, HR, Marketing, Support |

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
