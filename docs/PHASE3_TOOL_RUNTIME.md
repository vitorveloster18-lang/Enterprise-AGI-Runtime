# Fase 3 — Tool Runtime ✅

**Objetivo do documento original:** primeiras tools (filesystem, python, http,
database, process) e depois **browser, git, e-mail, MCP, ERP, CRM** — tudo passando
pelo Runtime.

## O que foi implementado

| Item | Situação | Onde |
|---|---|---|
| Filesystem / python / http / database / process | ✅ (Fase 1) | `tools/builtins/` |
| **Sandbox forte** (contêiner) | ✅ | `tools/sandbox.py` |
| **Git** (status, diff, log, commit) | ✅ | `tools/builtins/git_tool.py` |
| **E-mail** (SMTP envio, IMAP leitura) | ✅ | `tools/builtins/email_tool.py` |
| **Browser** (navigate, extract via Playwright) | ✅ | `tools/builtins/browser_tool.py` |
| **MCP** (cliente stdio JSON-RPC) | ✅ | `tools/mcp.py` + `egr mcp` |
| **Custo de ferramentas** | ✅ | `ToolResult.cost` → `TaskResult.tool_cost` |
| Políticas para as novas ferramentas | ✅ | `policies/defaults.py` (16 novas regras) |

## Sandbox: dois backends

| Modo | Como funciona | Quando usar |
|---|---|---|
| `container` | `docker/podman run --rm -i --network none --memory 512m --cpus 1 --pids-limit 128 --tmpfs /tmp -v workspace:/workspace:ro -v sandbox:/sandbox:rw` | produção, código gerado por modelo |
| `process` | subprocesso com ambiente filtrado (sem variáveis de segredo), `cwd` no sandbox, timeout | fallback quando não há runtime de contêiner |
| `auto` (padrão) | contêiner se o runtime estiver disponível e funcional; senão processo | default sensato |

```yaml
tools:
  sandbox:
    mode: auto                 # auto | container | process
    runtime: docker            # docker | podman
    image: python:3.11-alpine
    network: false
    memory: 512m
    cpus: '1'
    pids_limit: 128
    read_only_workspace: true
```

**Honestidade operacional:** em modo `process` o `egr doctor` reporta
`sandbox:isolamento` como **FALHA**. Isolamento de subprocesso não é fronteira de
segurança forte — é contenção por política + confinamento de paths. Para execução de
código não confiável, use contêiner.

## Novas ferramentas

### Git

```bash
egr policy test git.status          # allow  (leitura livre)
egr policy test git.commit          # require_approval (operator)
egr tool test git.status --execute
egr tool test git.log --arg limit=5 --execute
```

`git.commit` tem efeito colateral e nunca é automático — mesmo em development.

### E-mail

```yaml
tools:
  email:
    enabled: true
    smtp_host: smtp.exemplo.com
    smtp_port: 587
    use_tls: true
    username_env: EGR_SMTP_USER      # segredo vem do ambiente, nunca do YAML
    password_env: EGR_SMTP_PASS
    from_address: egr@acme.com
    cost_per_send: 0.002             # entra no custo da task
```

Envio é sempre `require_approval` e é bloqueado quando `external_ai: forbidden`.

### Browser

Requer Playwright (`pip install playwright && playwright install chromium`) e opt-in
em `tools.browser.enabled`. Suporta `allowed_domains` — qualquer domínio fora da lista
é recusado antes de abrir a página.

### MCP (Model Context Protocol)

Servidores MCP entram como Tools do Runtime, com nome `mcp.<servidor>.<ferramenta>`:

```yaml
mcp:
  enabled: true
  servers:
    - name: calculadora
      command: python
      args: [servers/calculadora.py]
```

```bash
egr mcp list                                          # servidores + ferramentas descobertas
egr mcp call mcp.calculadora.somar --arg a=2 --arg b=3 --execute
```

**Regra de ouro:** ferramentas MCP são descobertas em runtime e **não herdam permissão
nenhuma**. Sem regra de política, o default deny bloqueia. O workspace de exemplo tem
`policies/mcp.yaml` liberando o servidor local e exigindo aprovação em produção.

Transporte suportado: **stdio JSON-RPC 2.0** (`initialize` → `tools/list` → `tools/call`).
HTTP/SSE entra em uma próxima iteração.

## Custo de ferramentas

`ToolResult.cost` permite que qualquer ferramenta declare custo por chamada (APIs pagas,
e-mail, SMS). O Runtime acumula em `TaskResult.tool_cost` e expõe
`TaskResult.total_cost = cost (modelo) + tool_cost (ferramentas)`.

```python
ToolResult.success({"ok": True}, cost=1.25)
```

Assim o custo de uma automação inclui **modelo + ferramentas**, não só tokens.

## Políticas adicionadas (baseline)

| Ação | Decisão |
|---|---|
| `git.status` / `git.diff` / `git.log` | allow (somente leitura) |
| `git.commit` | require_approval (operator) |
| `email.send` | require_approval (operator) · deny se `external_ai: forbidden` |
| `email.read` | allow · require_approval em produção |
| `browser.navigate` / `browser.extract` | allow em development · require_approval nos demais |
| `mcp.*` | **nenhuma regra** → default deny (intencional) |

## Próximo passo (Fase 4)

Security + Policy completo: identidade, RBAC real, cofre de segredos e gestão de chaves.
O Policy Engine, o Data Boundary e as aprovações já existem; falta amarrar a identidade
do aprovador a papéis verificáveis.
