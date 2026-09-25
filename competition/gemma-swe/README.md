# Gemma 4 Developer Agent Competition — submissão EGR

Tradução da **fatia 3** do EGR (`task.delegate`: delegar → revisar →
accept/revise/escalate) para o formato de Agent Config (ADK) da competição.
Liga de **engenharia de agentes**: sem LoRA nesta versão (treino exige GPU
que não temos; ver `adapters/`).

## Mapa fatia 3 → ADK

| EGR (fatia 3) | Aqui | Arquivo |
|---|---|---|
| `orchestrator-agent` (delega, revisa) | `swe_orchestrator` (root) | `agent.yaml` + `prompts/system.md` |
| filha executa governada | `patch_coder` (sub-agente) | `sub_agents/patch_coder.yaml` |
| revisão accept/revise/escalate | `patch_reviewer` (veredito) | `sub_agents/patch_reviewer.yaml` |
| análise antes de agir | `code_analyzer` (só leitura + grafo) | `sub_agents/code_analyzer.yaml` |
| `max_rounds = 3`, profundidade 2 | rounds limitados, sem sub-delegação | prompts + `eval_config.yaml` |
| trilha auditada | `submit_patch` + resumo final | `prompts/system.md` |
| skills de navegação/verificação | `skills/repo_navigation`, `skills/patch_verify` | `skills/` |

## Como zipar

```bash
cd competition/gemma-swe
python check.py                    # valida schema, includes e referências
zip -r ../../submission.zip agent.yaml configs prompts sub_agents skills eval_config.yaml
```

O `agent.yaml` precisa estar na **raiz** do zip. `adapters/` entra quando
houver LoRA; `check.py` só usa stdlib (roda no PC do time sem instalar nada).

## Checklist de verificação (OBRIGATÓRIO antes de submeter)

O `HARNESS_README.md` e o `sample_submission/` só baixam com login no Kaggle —
este esqueleto foi escrito pelo schema público do ADK, então cada item abaixo
precisa de conferência contra os arquivos oficiais. Itens marcados `VERIFICAR`
no código correspondem a esta lista:

1. **Nome do modelo**: usamos `gemma-4-31b-it-qat-w4a16-ct` (confirmar exato).
2. **Referência de tools**: usamos `tools: [{name: run_command}, ...]`
   (confirmar nomes e se precisam de prefixo/namespace).
3. **Sub-agentes**: usamos `sub_agents: [{config_path: ...}]` (confirmar se o
   harness prefere `agent_tool` em `tools:` — há um exemplo comentado).
4. **`!include`**: confirmar diretiva e caminhos relativos.
5. **`generate_content_config`**: confirmar o nome da chave que carrega
   `configs/sampling.yaml` e os campos aceitos.
6. **`eval_config.yaml`**: formato inferido (regulamento menciona, schema
   desconhecido) — confirmar ou remover.
7. **Skills**: confirmar como o agente anexa skills (`run_skill_script` /
   `load_skill_resource`) e o frontmatter do `SKILL.md`.
8. **`adapter:`**: confirmar chave por agente (só quando houver LoRA).

## Estratégia de iteração (solo + API, sem GPU)

1. Baixar o dataset (aba **Data** da competição — aceitar as regras primeiro):
   primeiro `HARNESS_README.md` + `sample_submission/` (para a conferência
   acima), depois `tasks.jsonl` + `graphs/` + `embeddings/`. O resto
   (`docker/`, `wheels/`, `snapshots/`) só se precisar.
2. Rodar `localrun/inspect.py` no dataset e mandar a saída (adapta o harness
   aos formatos reais). Detalhes em `localrun/README.md`.
3. Iterar prompts/skills pelo placar local (`python -m localrun.run`), amostra
   pequena primeiro (custo da API!), escalando aos poucos.
4. LoRA só se o placar justificar: alugar A100 40GB por horas, treino pequeno
   e bem definido (orçamento estimado: US$100–300).

## Referências

- Dataset + HARNESS_README (login): <https://www.kaggle.com/competitions/gemma-4-developer-agent/data>
- Schema ADK Agent Config: <https://adk.dev/agents/config/> e
  <https://adk.dev/api-reference/agentconfig/>
- Scaffold público de referência (outro competidor, para estudar o harness
  local — não copiar a solução): <https://github.com/happyc0der/gemma-swe-agent>
