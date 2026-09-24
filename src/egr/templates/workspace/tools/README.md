# Ferramentas do workspace

Cada `*.py` aqui é uma ferramenta que o Runtime carrega na inicialização —
**depois de revalidar o código**. Ferramenta que não passa na verificação é
recusada com um evento no ledger (`dev.tool_rejected`) e nunca chega ao registry.

Ninguém escreve nesta pasta à mão por hábito: o caminho normal é

```bash
egr dev new tool exemplo.linhas      # cria uma proposta (rascunho)
egr dev validate <id>                # verificação estática
egr dev test <id>                    # executa isolada, em dry-run
egr dev approve <id>                 # humano aprova
egr dev apply <id>                   # grava aqui e recarrega o Runtime
```

## Regras para uma ferramenta

- uma classe por ferramenta, herdando de `Tool`, com `spec = ToolSpec(...)` e
  `execute(self, request, ctx)`;
- `ToolSpec.name` é literal e começa com o namespace do arquivo
  (`exemplo.py` declara `exemplo.*`);
- use `ctx.workspace`, `ctx.sandbox` e `ctx.artifacts` — nunca caminhos absolutos;
- respeite `ctx.dry_run`: não escreva nada quando ele for `True`;
- imports limitados à biblioteca padrão e ao próprio `egr`;
- nada de `eval`, `exec`, `subprocess`, `socket`, `pickle` ou atributos internos
  (`__class__`, `__globals__`, …);
- sem código solto no nível superior (só imports, definições e atribuições).

Arquivos começando com `_` são ignorados pelo carregamento.
