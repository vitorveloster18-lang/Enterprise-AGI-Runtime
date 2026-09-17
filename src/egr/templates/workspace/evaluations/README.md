# Avaliações

Suítes declaradas em YAML: o que executar, o que esperar e qual limite é
aceitável. O que vive no banco é a **execução medida** — casos, métricas,
comparação contra a baseline e achados de segurança.

```yaml
id: venda.preco
name: Cálculo de preço
target_kind: tool          # tool | workflow | agent | policy
target: venda.preco
cases:
  - id: com-desconto
    args: {quantidade: 2, unitario: 50, desconto: 10}
    expect_ok: true
    expect: ["output['total'] == 90.0"]
    max_duration_ms: 5000
thresholds:
  min_pass_rate: 1.0       # abaixo disso, o veredito é failed
  max_total_cost: 0.01
  max_p95_duration_ms: 3000
  max_regressions: 0       # casos que passavam e agora falham
```

```bash
egr eval smoke tool venda.preco   # gera a suíte mínima e roda
egr eval add avaliacoes.yaml      # registra uma suíte declarada
egr eval run venda.preco          # executa, compara e dá o veredito
egr eval runs | show <run>        # histórico medido
egr eval baseline <run>           # promove uma execução a referência
egr eval security agent finance-agent
```

## Contexto das expectativas

| alvo | campos disponíveis |
|---|---|
| `tool` | `ok`, `output`, `error`, `cost`, `duration_ms`, `files` |
| `workflow` | `ok`, `status`, `steps`, `outputs`, `cost`, `duration_ms` |
| `agent` | `ok`, `status`, `answer`, `steps`, `tools`, `cost` |
| `policy` | `decision`, `allowed`, `needs_approval`, `reason`, `rule_id` |

As expressões usam a gramática segura das condições de política (sem `eval`):
`output['total'] == 90.0`, `contains(answer, 'nota')`, `cost <= 0.01`,
`decision == 'require_approval'`, `status != 'failed'`.

## Vereditos

`passed` (dentro de todos os limites) · `failed` (abaixo do mínimo) ·
`regressed` (piorou em relação à baseline) · `error` (nem deu para avaliar).
