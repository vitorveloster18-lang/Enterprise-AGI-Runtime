# localrun — mini-harness via API (sem GPU)

Itera os prompts/skills que vão no zip, usando o Gemma via Google AI Studio.
Os prompts são os **mesmos arquivos** de `../prompts/` — editar ali muda o
harness e a submissão juntos (fonte única de verdade).

## Rodar (num Linux com Python + git — não no Termux)

```bash
cd competition/gemma-swe/localrun
export GEMINI_API_KEY=...            # nunca commitar, nunca mandar no chat
export TASKS_JSONL=/caminho/tasks.jsonl
export DATA_DIR=/caminho/data        # graphs/, embeddings/, snapshots/
python inspect.py "$DATA_DIR"        # PRIMEIRO: mande a saída no chat
python -m localrun.run --list        # confere as tasks
python -m localrun.run --tasks 3     # primeira rodada (amostra pequena!)
```

## Tetos (env, com padrão seguro)

| Var | Padrão | Significado |
|---|---|---|
| `GEMMA_MODEL` | `gemma-4-31b-it` | modelo na API (ajuste ao exato) |
| `GEMMA_BASE_URL` | `.../v1beta/openai` | endpoint OpenAI-compatível |
| `MAX_TASKS` | `3` | tasks por rodada |
| `MAX_TURNS` | `40` | turnos de tool-call por task |
| `TASK_TIMEOUT` | `1500` | segundos por task |
| `MAX_TOKENS` | `200000` | para tudo ao atingir |
| `TEMPERATURE` | `0.0` | determinístico (iteração comparável) |

## Custo

Cada task consome dezenas de milhares de tokens (contextos longos, 3 papéis,
até 3 rounds). Comece com `--tasks 3`, olhe o placar e os tokens, só então
escale. Acompanhe o uso no painel do AI Studio.

## Limites conhecidos

- A API não é o `qat-w4a16-ct` quantizado do avaliador: prompt transfere bem,
  mas o comportamento não é idêntico — margem de surpresa no placar oficial.
- `search_similar_code` usa fallback lexical até os embeddings reais serem
  ligados (após o `inspect.py`).
- Formatos de `tasks.jsonl`/snapshots/grafos são adaptados após a inspeção:
  o `tasks.py`/`graph.py` atuais cobrem os formatos mais prováveis e falham
  com mensagem dizendo o que mandar.
