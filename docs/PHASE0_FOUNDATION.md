# Fase 0 — Foundation ✅

**Objetivo:** criar o projeto Python, o monorepo e a estrutura de workspace.

## Entregáveis

| Entregável | Situação | Onde |
|---|---|---|
| Projeto Python (src layout, pyproject, scripts) | ✅ | `pyproject.toml`, `src/egr/` |
| CLI `egr init` | ✅ | `cli/commands/workspace.py` + `templates/` |
| CLI `egr status` | ✅ | `Runtime.status()` |
| CLI `egr doctor` | ✅ | `Runtime.health()` |
| Estrutura de diretórios | ✅ | `render_workspace()` |
| Migrações versionadas | ✅ | `src/egr/migrations/001_init.sql` |
| Provider offline para desenvolvimento | ✅ | `models/providers/echo.py` |

## Estrutura criada

```
egr/
├── pyproject.toml · requirements.txt · Makefile
├── migrations/                 # (gerenciadas em src/egr/migrations, empacotadas)
├── src/egr/
│   ├── core/                   # ids, tempo, hash, caminhos, config, logging, erros
│   ├── domain/                 # objetos fundamentais
│   ├── storage/                # database, migrations, repositories
│   ├── policies/               # engine, condições, defaults, loader
│   ├── tools/                  # protocolo, registry, builtins
│   ├── models/                 # gateway + providers
│   ├── memory/                 # serviço de memória
│   ├── runtime/                # runtime, agent/task engine, planner, events, loader
│   ├── security/               # redação, fronteira de dados, segredos
│   ├── audit/                  # ledger
│   ├── api/                    # FastAPI + console
│   ├── cli/                    # comandos
│   ├── templates/workspace/    # esqueleto do workspace
│   └── extensions/             # (Fase 3+)
└── tests/                      # 30 testes
```

## `egr init` cria

```
workspace/
├── egr.yaml
├── agents/{runtime,document,finance}-agent.yaml
├── policies/finance.yaml
├── workflows/invoice-processing.yaml
├── documents/*.md                # amostra do vertical contábil
├── artifacts/ · logs/ · .egr/sandbox
└── README.md
```

## Critério de saída

`egr init` → `egr status` → `egr doctor` verde. **Atingido.**
