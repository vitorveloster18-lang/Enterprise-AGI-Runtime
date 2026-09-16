# EGR Workspace

Este diretório é um **workspace do Enterprise AGI Runtime**: um ambiente local,
versionado e auditável onde agentes executam trabalho real.

```
.
├── egr.yaml            # configuração (enterprise, ambiente, modelos, segurança)
├── agents/             # declaração dos agentes (fonte da verdade)
├── policies/           # políticas de autorização (default deny)
├── workflows/          # processos (como o trabalho acontece)
├── documents/          # dados de entrada do workspace
├── artifacts/          # saídas produzidas pelos agentes
├── logs/               # logs estruturados (JSONL)
└── .egr/               # estado do runtime (banco + sandbox)
```

## Ciclo básico

```bash
egr status                                              # estado do runtime
egr doctor                                              # saúde
egr task "Analise os documentos desta pasta e produza um relatório."
egr task list
egr task inspect <task_id>
egr audit verify                                        # integridade da cadeia de auditoria
```

## Governança

- Nenhuma ação é executada sem passar pela **Policy Engine**.
- Ações de risco fora de `development` exigem **aprovação humana**.
- Tudo é registrado em um **ledger append-only** com hash encadeado.
- Promoção para produção nunca é automática (`dev → staging → proposta → humano → produção`).
