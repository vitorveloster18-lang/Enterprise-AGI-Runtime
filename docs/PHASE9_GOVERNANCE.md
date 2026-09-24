# Fase 9 — Production Governance

**Status:** concluída (2026-09). 233 testes · `ruff` limpo.

A Fase 7 responde *"pode entrar?"* e a Fase 8 *"continua bom?"*. A Fase 9 responde
*"quem autorizou isso chegar em produção, e como a gente volta?"*.

> Deploy automático não existe no EGR. Um artefato não "vai para produção": ele
> entra em um **release** que carrega versão, evidência e a assinatura de quem
> decidiu.

---

## 1. A escada

```
development ──► staging ──► production
     (Fase 7)      (Fase 9)     (Fase 9)
```

| Regra | Por quê |
|---|---|
| ninguém pula degrau | `rank(target) - rank(origem) == 1` — staging existe para ser o lugar onde o erro é barato |
| produção exige staging aplicado | o gate confere se já houve release `deployed` em staging para aquele item |
| promoção nunca é automática | o único caminho é `create → submit → approve (humano) → deploy` |
| rollback restaura snapshot | voltar não é "desfazer na mão", é escrever de volta a revisão capturada |

---

## 2. O objeto: `Release`

```
Release
├── items[]      artefatos versionados (kind, name, version, revision, fingerprint, de → para)
├── checks[]     gates conferidos (reaproveitam ValidationCheck da Fase 7)
├── evidence[]   ids das execuções de avaliação aprovadas (Fase 8)
├── decidido por / quando / nota
└── deployed_at · rolled_back_at · rollback_of
```

Ciclo e estados:

```
draft ──submit──► submitted ──approve──► approved ──deploy──► deployed
  │                   │                                          │
  └──reject──► rejected                                rollback ─┴──► rolled_back
```

`failed` existe para o release que nasce reprovado nos gates e é abandonado.

---

## 3. Gates de promoção

Conferidos em `create` e reconferidos em `check`/`submit`/`deploy` — a evidência
pode apodrecer entre a criação e a aplicação.

| Gate | O que exige |
|---|---|
| `itens` | release com pelo menos um artefato |
| `escada` | destino sobe exatamente um degrau a partir do ambiente declarado |
| `estágio` | produção só depois de um release aplicado em staging |
| `artefato` | o arquivo existe no workspace |
| `evidência` | existe avaliação `passed` para aquele alvo **e** ela é da versão atual |
| `segurança` | nenhum achado bloqueante na varredura da Fase 8 (`curinga`, `sem-memoria`, …) |

Gate que falha é **impedimento declarado** no release, não exceção escondida:
`egr release show` lista o que falta.

---

## 4. Versão e rollback

Versão é **snapshot do conteúdo**, identificada por `sha256[:16]`:

- `snapshot` só cria revisão nova quando o conteúdo mudou (senão é no-op);
- artefato que declara `version:` no YAML usa esse rótulo; ferramenta (código
  Python, sem YAML) usa `r<n>`;
- promoção aplica o conteúdo exato do snapshot — não "o que está no disco" — e
  reescreve a linha `environment:`;
- rollback restaura a revisão capturada quando o release foi criado (o estado
  imediatamente anterior a ele) e devolve o ambiente de origem.

Tudo versionado fica em `artifact_versions`, com o id `<kind>:<nome>@<revisão>`.

---

## 5. Quem pode aprovar

Mesma régua de qualquer decisão crítica do Runtime:

| Situação | Resultado |
|---|---|
| `security.identity_required: false` | o ator é registrado como está (trilha auditada, sem verificação) |
| `identity_required: true` e sem principal | **401** — `identidade não verificada` |
| principal sem `release.promote` | **403** — `não tem a permissão 'release.promote'` |
| agente aprovando (`allow_agent_approval: false`) | **403** — `não pode aprovar promoção` |
| staging | exige pelo menos `operator` |
| produção | exige `approval_min_role` (padrão: `approver`) |

Papéis: `viewer < operator < approver`, com `admin` satisfazendo todos.

---

## 6. Superfície

### CLI

```bash
egr release status [--json]                      # escada, releases, versões, aplicados
egr release create workflow:invoice-processing --to staging --reason "..."
egr release check <id>                           # reconfere os gates
egr release submit <id>                          # falha se os gates reprovarem
egr release approve <id> --by vitor --token egr_... --note "..."
egr release reject <id> --by vitor --note "..."
egr release deploy <id> --by vitor
egr release rollback <id> --by vitor --note "..."
egr release list [--status deployed] [--json]
egr release show <id> [--json]
egr release versions workflow invoice-processing [--json]

egr proposal create|verify|prove|approve|reject|apply|list|show   # Fase 7
egr proposal deploy <proposta> --to staging                       # atalho para o release
egr proposal rollback <proposta>                                  # atalho para o rollback
egr lifecycle dev|stage|production                                # visão da escada
```

### API

| Método | Rota | Papel |
|---|---|---|
| GET | `/v1/release` | escada, contagens, versões e o que está aplicado |
| POST | `/v1/release` | cria release (snapshot + gates) |
| GET | `/v1/releases` | histórico de promoções |
| GET | `/v1/release/{id}` | release completo (itens, gates, evidência, decisão) |
| POST | `/v1/release/{id}/check` | reconfere os gates |
| POST | `/v1/release/{id}/submit` | submete à aprovação |
| POST | `/v1/release/{id}/approve` | aprovação humana (401/403 conforme o caso) |
| POST | `/v1/release/{id}/reject` | recusa |
| POST | `/v1/release/{id}/deploy` | aplica |
| POST | `/v1/release/{id}/rollback` | volta para a revisão anterior |
| GET | `/v1/release/versions/{kind}/{name}` | snapshots do artefato |

### Runtime

| Método | Devolve |
|---|---|
| `runtime.release_manager` | `create / check / submit / approve / reject / deploy / rollback / get / list` |
| `runtime.release_manager.versions` | `snapshot / restore / versions / current / set_environment` |
| `runtime.governance_status()` | escada, releases, versões, aplicados e postura de identidade |
| `runtime.health()` | check `governance` com contagem de releases e versões |

---

## 7. Eventos auditados

| Evento | Quando |
|---|---|
| `release.created` | release criado (com itens, nº de gates e erros) |
| `release.submitted` | submetido (e reavaliação de gates) |
| `release.approved` | aprovação humana (com `identidade_verificada`) |
| `release.rejected` | recusa |
| `release.deployed` | aplicação no ambiente (com as evidências) |
| `release.rolled_back` | reversão (com o release de origem) |
| `artifact.versioned` | snapshot criado ou restaurado (impressão digital, revisão) |

---

## 8. Verificação

```bash
.venv/bin/pytest -q                      # 233 testes (41 da Fase 9)
.venv/bin/ruff check src tests           # limpo

# caminho completo em um workspace limpo
egr init /tmp/f9 && cd /tmp/f9
egr eval smoke workflow invoice-processing
egr release create workflow:invoice-processing --to staging
egr release submit <id> && egr release approve <id> --by human:vitor
egr release deploy <id> --by human:vitor
egr release create workflow:invoice-processing --to production
egr release submit <id> && egr release approve <id> --by human:vitor
egr release deploy <id> --by human:vitor
egr release rollback <id> --by human:vitor
```

Pulo de degrau e falta de evidência foram conferidos ao vivo: o release nasce
`draft` com os impedimentos listados, e `submit` recusa.

---

## 9. Lacunas declaradas

| Lacuna | Situação |
|---|---|
| promoção multi-artefato atômica | os itens são aplicados em sequência; um rollback parcial deixa os anteriores reverterem junto, mas não há transação de disco |
| políticas por ambiente | o gate é fixo (escada, evidência, segurança); regras por empresa entram como política declarada numa fase seguinte |
| assinatura do aprovador | a decisão fica na trilha encadeada, mas não há assinatura criptográfica dorelease |
| evidência por ambiente | a evidência é ligada à **versão** do artefato: promover dev→staging→produção não exige nova avaliação, mesmo que a política mude por ambiente (`python.execute` passa a exigir aprovação em staging) — ao reavaliar, a Fase 8 mede essa diferença |
| promoção de workspace remoto | o release promove dentro do workspace; publicar em outro Runtime depende da Fase 11 (integrações) |
| aprovação por quórum | hoje é um humano com papel suficiente; quórum/4-olhos é evolução de política |

---

## 10. Decisões

| # | Decisão | Motivo |
|---|---|---|
| ADR-033 | promoção é release com gates, nunca comando direto | deixa rastro do que foi promovido, com qual evidência e por quem |
| ADR-034 | versão é snapshot de conteúdo (impressão digital) | rollback restaura o que existia, sem reconstruir |
| ADR-035 | produção exige `approval_min_role` e staging aplicado | o degrau mais caro é o mais vigiado |
| ADR-036 | rollback restaura a revisão capturada pelo release | "voltar" tem que ser determinístico, não arqueologia |
