# Fase 4 — Security + Policy ✅

**Objetivo do documento original:** segurança séria — isolamento, secrets, RBAC,
auditabilidade e política. Sem isso, nada disso é enterprise.

A Fase 4 fecha o ciclo que faltava: **"humano aprova o crítico" deixa de ser uma
string num campo e passa a ser um fato verificável**.

| Item | Situação | Onde |
|---|---|---|
| **Identidade verificável** (principal + token com hash) | ✅ | `security/identity.py` |
| **RBAC real** (papel → permissão, checado na decisão) | ✅ | `security/rbac.py` |
| **Cofre de segredos** (cifrado em repouso) | ✅ | `security/vault.py` + `security/crypto.py` |
| **Gestão de chaves** (init/status/rotate com recifragem) | ✅ | `security/keystore.py` |
| Data Boundary (classificar/minimizar/sanitizar) | ✅ (Fase 1) | `security/data_boundary.py` |
| Policy Engine com default deny | ✅ (Fase 1) | `policies/engine.py` |
| Aprovação como objeto de primeira classe | ✅ (Fase 1) | `domain/approval.py` |

## 1. Identidade verificável

```bash
egr identity add vitor --name "Vitor" --roles approver
egr identity add ci-bot --kind service --roles operator
egr identity token vitor --ttl-days 30 --label notebook
#   token: egr_tkn_20260916..._<id>.<segredo>     ← aparece uma única vez
egr identity whoami --by egr_tkn_..._xyz
```

* o **segredo nunca é persistido** — só `sha256(salt || segredo)`, comparado com
  `hmac.compare_digest` (tempo constante);
* o token carrega o `token_id` em claro, o que permite **revogação pontual**
  (`egr identity revoke-token <id>`) sem invalidar tudo;
* expiração por TTL (`--ttl-days`, `0` = sem expiração);
* principal desabilitado derruba os tokens na hora (desligamento de funcionário).

Tipos: `human`, `agent`, `service`. **Agentes nunca aprovam o próprio trabalho**
(`security.allow_agent_approval`, padrão `false`).

## 2. RBAC

```bash
egr security roles                  # matriz completa
egr security roles --role approver  # herança + permissões
```

| Papel | Herda | Permissões centrais |
|---|---|---|
| `viewer` | — | `task.read`, `policy.read`, `agent.read`, `audit.read`, `memory.read` |
| `operator` | viewer | `task.submit`, `task.cancel`, `tools.execute`, `memory.write`, `policy.sync` |
| `approver` | operator, viewer | **`approval.decide`** |
| `auditor` | viewer | leitura ampla de auditoria |
| `security_admin` | operator, viewer | `secret.*`, `key.manage`, `identity.manage`, `policy.write` |
| `admin` | todos | todas |

Papéis são **aditivos e explícitos**; `admin` satisfaz qualquer `required_role`
exigido por uma política.

## 3. Cofre de segredos

```bash
egr key init                                        # chave mestra (0600, fora do YAML/DB)
printf 'sk-...' | egr secret set openai --provider openai --stdin
egr secret list                                     # metadados — nunca o valor
egr secret show openai --reveal                     # valor só quando pedido
egr secret rotate openai --stdin
egr key rotate                                      # nova chave + recifra todo o cofre
```

Uso no `egr.yaml` (a credencial **não** está no arquivo):

```yaml
models:
  providers:
    - name: cloud
      type: openai_compat
      base_url: https://api.openai.com/v1
      api_key_env: vault:openai     # <- cofre, não variável de ambiente
```

Resolução de referências (`Runtime.resolve_secret`):

| Referência | Origem |
|---|---|
| `vault:NOME` | cofre cifrado do workspace |
| `env:VARIAVEL` | ambiente do processo |
| `VARIAVEL` | compatível com o `api_key_env` anterior |

## 4. Criptografia (ADR-018)

Envelope `EGR1.<nonce>.<cifra>.<tag>`:

```
chave_mestra ──scrypt(salt próprio do segredo)──> (k_fluxo, k_mac)
cifra = texto ⊕ HMAC-SHA256(k_fluxo, nonce || contador)
tag   = HMAC-SHA256(k_mac, "EGR1." || nonce || cifra)
```

Encrypt-then-MAC com verificação em tempo constante: adulterar um byte do
envelope derruba a autenticação e o cofre **falha fechado** (`VaultError`).

## 5. Enforcement: quem pode decidir o crítico

Com `security.identity_required: true`, `Runtime.approve/deny` exigem:

1. um **principal autenticado** (token válido, não expirado, não revogado);
2. a permissão **`approval.decide`**;
3. um papel que satisfaça o `required_role` da política;
4. não ser um agente (salvo `allow_agent_approval: true`).

Falhas geram `AuthorizationError` (CLI sai com código `3`, API responde `401/403`),
gravam `security.authorization_denied` na auditoria e **a aprovação continua
pendente**.

```bash
$ egr approval approve apr_... --by alguem
✗ decisão recusada: identidade não verificada: decisão exige um principal autenticado
$ egr approval approve apr_... --by ci-bot --token egr_...
✗ decisão recusada: 'ci-bot' não tem a permissão 'approval.decide'
$ egr approval approve apr_... --by vitor --token egr_...
✓ aprovação apr_... registrada
```

Com a exigência desligada (padrão de desenvolvimento), o Runtime continua
funcionando, mas o evento `human.decision` fica marcado com
`identity_verified: false`. Ou seja: **a auditoria nunca mente sobre o nível de
garantia da decisão**.

## 6. Postura exposta (e lacunas assumidas)

```bash
egr security status     # principais, tokens, chave, cofre, lacunas
```

Os checks `security:chave`, `security:identidade` e
`security:chave_permissoes` entram no `egr doctor`. A API expõe
`/v1/security`, `/v1/security/roles`, `/v1/principals` e
`/v1/principals/whoami`, e aceita `Authorization: Bearer egr_...` na decisão de
aprovações (o console guarda o token em `localStorage`).

**Honestidade operacional:** o padrão é `identity_required: false` para não
quebrar o fluxo de desenvolvimento, e o próprio Runtime lista isso como lacuna
em `egr security status` e como check falho no `egr doctor`.

## Correção incluída: MCP e o diretório de trabalho

`MCPClient` iniciava o servidor com o **cwd do processo**, então `egr serve` chamado
de fora do workspace quebrava `./servers/calculadora.py` com um erro opaco
("closed the connection"). Agora o cliente recebe a raiz do workspace e a falha
reporta contexto acionável (caminho do script, comando, dependências).

## Próximo passo (Fase 5)

Memory System completo: memória episódica, conhecimento, aprendizado operacional
e avaliação do que merece ser lembrado.
