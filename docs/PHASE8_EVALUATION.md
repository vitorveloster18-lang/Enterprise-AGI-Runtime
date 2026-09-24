# Fase 8 — Evaluation

**Status:** concluída (2026-09). 192 testes · `ruff` limpo.

A Fase 7 responde *"pode entrar?"* — o artefato é seguro. A Fase 8 responde
*"continua bom?"* — com números, limites declarados e uma referência para
comparar.

> Segurança sem avaliação é porta trancada com a casa pegando fogo: ninguém
> percebe que o trabalho piorou até alguém reclamar.

---

## 1. O objeto

```
suíte → casos medidos → métricas → limites → baseline → veredito
```

| Conceito | O que é |
|---|---|
| `EvaluationSuite` | o que executar, o que esperar e qual limite é aceitável |
| `EvaluationRun` | a execução medida: casos, métricas, comparação e achados |
| `Thresholds` | mínimo de acerto, teto de custo, teto de p95, regressões toleradas |
| baseline | uma execução aprovada promovida a referência |
| veredito | `passed` · `failed` · `regressed` · `error` |

```yaml
id: venda.preco
name: Cálculo de preço
target_kind: tool            # tool | workflow | agent | policy
target: venda.preco
cases:
  - id: com-desconto
    args: {quantidade: 2, unitario: 50, desconto: 10}
    expect_ok: true
    expect: ["output['total'] == 90.0"]
    max_duration_ms: 5000
thresholds:
  min_pass_rate: 1.0
  max_total_cost: 0.01
  max_p95_duration_ms: 3000
  max_regressions: 0
```

Expectativas usam a **mesma gramática segura** das condições de política — sem
`eval`, sem imports, sem acesso a atributos.

---

## 2. O que cada alvo executa

| alvo | o caso executa | contexto das expectativas |
|---|---|---|
| `tool` | a ferramenta, isolada no sandbox (Fase 7/3) | `ok`, `output`, `error`, `cost`, `duration_ms`, `files` |
| `workflow` | um **run real** — cada passo é uma task auditada | `ok`, `status`, `steps`, `outputs`, `cost`, `duration_ms` |
| `agent` | uma task com o agente | `ok`, `status`, `answer`, `steps`, `tools`, `cost` |
| `policy` | uma decisão do Policy Engine | `decision`, `allowed`, `needs_approval`, `reason`, `rule_id` |

Erro do artefato é **resultado** (pode ser até o esperado: "recusa argumento
inválido"); exceção de infraestrutura é falha da avaliação, e o caso é marcado
como tal. Alvo ausente vira `error` — "não deu para avaliar" é diferente de
"reprovou".

---

## 3. Métricas

```
total · passed · failed · pass_rate
total_cost · avg_cost
avg_duration_ms · p95_duration_ms · max_duration_ms
error_rate · failed_cases
```

Números simples, comparáveis entre execuções — é o que permite dizer "piorou".

---

## 4. Regressão

Toda execução é comparada com a **última execução aprovada** da mesma suíte (ou
com uma baseline explícita). O relatório lista:

- casos que passavam e agora falham (`new_failures`);
- casos que falhavam e agora passam (`fixed`);
- variação de taxa de acerto, custo e latência.

Latência acima de `max_latency_drift_pct` (padrão 25%) conta como regressão.
Quando há regressão, o veredito é **`regressed`** — diagnóstico mais específico
que `failed`, com os motivos de limite mantidos na lista.

Exemplo real (ferramenta alterada para ignorar o desconto):

```
veredito       regressed
casos          2/3
baseline       eva_20260917011017_a31a75
regressões     1
• 1 caso(s) que passavam agora falham: com-desconto
• taxa de acerto caiu 33.3 pontos percentuais
```

---

## 5. Segurança

`egr eval security <alvo> <nome>` varre o artefato do ponto de vista do Runtime
— não é scanner de vulnerabilidades, é a conferência de que ele não foi escrito
para enfraquecer o governo:

| alvo | o que é declarado |
|---|---|
| `tool` | revalidação estática do código, risco sem `side_effects`, efeito sem política, parâmetros ausentes |
| `workflow` | grafo inválido, agente inexistente, ferramenta fora do alcance do agente, passo vazio, produção sem política |
| `agent` | `*` em `permissions.tools`, sem objetivo, sem memória, capacidade sem provedor, produção indevida |
| `policy` | regra que libera tudo (`action: "*"` com `allow`), regra sem justificativa, política sem regras |

Achado `critical` reprova a execução **mesmo que todos os casos passem**.

---

## 6. Suítes derivadas da declaração

Nenhum artefato novo entra sem **alguma** prova. O Runtime infere a suíte
mínima do que o próprio artefato declara:

- **tool:** recusa chamada sem parâmetros obrigatórios; responde a uma chamada
  válida (amostra gerada dos tipos declarados); não estoura exceção não tratada;
- **workflow:** roda até o fim sem `failed`;
- **agent:** executa uma task até o fim (prova o *encanamento*: política,
  ferramentas, memória — **não** a qualidade do modelo);
- **policy:** cada regra **incondicional e sem sombra** decide o que declara.

> Regra com `condition` depende de contexto (`amount`, `environment`,
> `external_ai`) que o Runtime não inventa. Ação com mais de uma regra depende
> da ordem. Nos dois casos o gerador **declara a lacuna** na suíte
> (`metadata.fora_do_escopo`) em vez de fabricar um caso que falha por motivo
> errado.

---

## 7. Superfície

**CLI** — `egr eval smoke|add|list|run|runs|show|baseline|security`
(`run --json` para automação; `runs --json` devolve ids completos).

**API** (tag `evaluation`):

```
GET    /v1/eval
GET    /v1/eval/suites                       POST /v1/eval/suites
POST   /v1/eval/suites/{id}/run              (baseline opcional)
GET    /v1/eval/runs                         GET /v1/eval/runs/{id}
POST   /v1/eval/runs/{id}/baseline
GET    /v1/eval/security/{target_kind}/{target}
```

**Eventos** — `eval.run_started`, `eval.run_finished`, `eval.regression`,
`eval.security_finding`.

**Persistência** — migração `007_evaluation.sql` (`evaluation_suites`,
`evaluation_runs`).

**`egr doctor`** — uma linha de diagnóstico por suíte cujo último veredito não
foi `passed`.

---

## 8. Verificação

25 testes novos (`tests/test_phase8_evaluation.py`), entre eles:

- suíte derivada da declaração; amostra de argumentos por tipo declarado;
- política: regras condicionais e com sombra ficam **fora** do escopo declarado;
- caso que espera falha passa quando a ferramenta recusa (erro do artefato ≠
  falha de infraestrutura);
- limite de taxa de acerto e de p95 reprovam; limite folgado aprova;
- segunda execução detecta a regressão e aponta o caso;
- baseline explícita inexistente é erro; deriva de latência conta como regressão;
- alvo ausente ⇒ `error`, não `failed`;
- workflow avaliado executa um run real; política simula decisões; agente roda
  uma task;
- achado `critical` reprova a execução mesmo com os casos passando.

---

## 9. Lacunas declaradas

| Lacuna | Destino |
|---|---|
| Qualidade de **modelo** (a avaliação de agente prova o encanamento; com provedor local não há como medir a resposta) | laboratório de avaliação com casos humanos e modelos reais |
| Simulação de carga/concorrência e latência sob estresse | Fase 9 / operação |
| Custo real de modelo por caso (hoje o custo vem das ferramentas; provedores locais não cobram) | Fase 11 (integrações pagas) |
| Benchmark comparativo entre versões de um mesmo agente | evolução natural da baseline (Fase 9) |
| Avaliação automática no CI do workspace | Fase 9 (promoção entre ambientes) |

O que a Fase 8 **não** faz: não aprova artefato (isso é humano), não inventa
contexto para regra condicional, e não confunde "rodou" com "está bom" — o
veredito existe porque a suíte declarou o que é bom.

---

## 10. Decisões

- **ADR-030** · Qualidade é medida, não declarada: avaliação como objeto auditado
- **ADR-031** · Regressão se mede contra baseline explícita, não contra memória
- **ADR-032** · Achado crítico de segurança reprova mesmo com casos passando
