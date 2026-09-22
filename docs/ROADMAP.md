# Roadmap — Fases 0 a 12 + Fase 13 (fechamento das lacunas)

Situação em 2026-09: **Fases 0 a 12 implementadas e testadas** e **Fase 13 concluída**
(654 testes). A Fase 13 não é fase nova: é o fechamento das lacunas que o próprio
roadmap declarou — detalhado em [`docs/PHASE13_LACUNAS_FECHADAS.md`](PHASE13_LACUNAS_FECHADAS.md).

| Fase | Nome | Situação | Observação |
|---|---|---|---|
| 0 | Foundation | ✅ | monorepo Python, `egr init/status/doctor`, migrações, templates |
| 1 | Runtime Core | ✅ | Task → Agent → Plan → Policy → Tool → Memory → Audit |
| 2 | Model Gateway | ✅ | roteamento por capacidade/custo/local_first, precificação, telemetria (`model_calls`), orçamento por task e por dia, `egr model usage` |
| 3 | Tool Runtime | ✅ | filesystem, python, http, process, database, **browser, git, e-mail, MCP** + sandbox forte (contêiner) e custo por ferramenta |
| 4 | Security + Policy | ✅ | identidade verificável (token com hash), RBAC checado na decisão, cofre cifrado (`EGR1`), gestão/rotação de chaves, `egr security/identity/secret/key` |
| 5 | Memory | ✅ | knowledge/operational/episodic/**semantic** com embedding local determinístico, recuperação híbrida (BM25 + cosseno via RRF), ciclo de vida (reforço/decaimento/saliência) e consolidação com arquivamento |
| 5b | — | ✅ | memória multimodal (binário em `artifacts/media`, tipo pelos bytes, legenda buscável) e limpeza de PII na escrita (CPF/CNPJ por dígito verificador, cartão por Luhn; registra tipo, nunca valor) — fechado na Fase 13 |
| 6b | — | ✅ | coordenação negociada (lances declarados por custo e fila, quatro estratégias, handoff com motivo e limite) e gatilhos de banco (`CREATE TRIGGER` no SQLite → fila `db_events` → evento do Runtime) — fechado na Fase 13 |
| 8b | — | ✅ | laboratório: qualidade por similaridade ou juiz de modelo (com degradação declarada), comparação de provedores e simulação de carga com p50/p95/p99 — fechado na Fase 13 |
| 9b | — | ✅ | promoção assinada (HMAC do manifesto com a chave mestra) e quórum de aprovação: produção exige dois votos de pessoas diferentes, recusa é veto e o deploy confere que o conteúdo é o aprovado — fechado na Fase 13 |
| 10b | — | ✅ | anexos e mídia entrando por lista branca (`artifacts/inbox`, impressão digital, recusa com motivo) e botões que viram comando governado — fechado na Fase 13 |
| 11b | — | — | catálogo de ERP/CRM/RH prontos entra com os Packs Verticais (Fase 12); fila de saída com retry/backoff também foi entregue na Fase 12 |
| 12b | — | ✅ | dreno da fila em processo explícito (`egr integration worker`) e atualização de pack que preserva o que foi editado — fechado na Fase 13 |
| 6 | Orchestration | ✅ | execução como objeto auditado, DAG com condição/retry/compensação/tolerância, paralelismo por nível, retomada e cancelamento, scheduler cron idempotente, gatilho por evento e webhook |
| 7 | Development Environment | ✅ | proposta verificada (AST) + prova em sandbox + aprovação humana; agentes criam agents/tools/workflows sem nunca aplicar |
| 8 | Evaluation | ✅ | suítes com casos medidos, métricas (acerto/custo/p95), limites, baseline e regressão, varredura de segurança do artefato |
| 9 | Production Governance | ✅ | release com gates (escada, evidência, segurança), versão por snapshot e rollback; produção exige staging aplicado + papel mínimo |
| 10 | Remote Control | ✅ | gateway de canais: Telegram (polling/webhook), Slack (Events API assinada), Web (/chat) e terminal, com pareamento e RBAC |
| 11 | Enterprise Integrations | ✅ | conectores declarados (REST/GraphQL/SQL/webhook): lista branca de host/método, credencial no cofre, política por operação, custo/latência/decisão registrados, eventos de entrada assinados e idempotentes |
| 12 | Vertical Packs | ✅ | 7 packs (Finanças, Contabilidade, Vendas, Operações, RH, Marketing, Suporte) instalados por proposta aprovada + fila de saída com retry/backoff |
| 13 | Fechamento das lacunas | ✅ | **Fase 13 concluída**: 10b, 12b, 8b, 9b, 6b e 5b entregues (anexos/botões; worker e pack; laboratório de avaliação; assinatura e quórum; coordenação e gatilhos de banco; memória multimodal e PII) — nenhuma lacuna declarada em aberto |

Legenda: ✅ concluído · 🟡 parcial (base pronta) · ⏳ não iniciado

## Sequência recomendada

```
[V1] Fases 0-6     Runtime validado executando trabalho real (0-6 feitas)
[V2] Fases 7-11    Plataforma empresarial (console, RBAC, integrações, multi-agente)
[V3] Fase 12       Empresa autônoma: o Runtime constrói e evolui automações
[V4] Fase 13       Nenhuma promessa em aberto: as lacunas declaradas, fechadas
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
