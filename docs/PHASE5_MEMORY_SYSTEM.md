# Fase 5 — Memory System ✅

**Objetivo do documento original:** memória da empresa (knowledge, operational,
episodic, semantic), o que merece ser lembrado, consolidação e avaliação.

A memória do EGR é da empresa, não do modelo: isolada por namespace, versionada
no banco local, auditada e governada por política de ciclo de vida.

| Item | Situação | Onde |
|---|---|---|
| Knowledge / operational / episodic | ✅ (Fase 1) | `domain/memory.py` |
| **Semantic** com embedding local | ✅ | `memory/embeddings.py` |
| **Recuperação híbrida** (BM25 + cosseno) | ✅ | `memory/retrieval.py` |
| **Ciclo de vida** (reforço, decaimento, saliência) | ✅ | `memory/salience.py` |
| **Consolidação** (near-duplicatas, arquivamento, poda) | ✅ | `MemoryService.consolidate` |
| **Reindexação** de vetores | ✅ | `egr memory reindex` |
| Auto-remember / auto-recall no Runtime | ✅ | `runtime/agent_engine.py` |

## 1. Embedding local e determinístico (ADR-021)

Nenhuma dependência externa, nenhum download, nenhum dado saindo da máquina:

```
texto -> normalizar (sem acentos) -> stopwords PT-BR -> stemmer leve
      -> palavras (1.0) + bigramas (0.5) + char 4-gramas (0.1)
      -> blake2b -> índice ± -> tf sublinear -> normalização L2
```

* **Determinístico**: `blake2b`, nunca `hash()` do Python (que varia por processo).
* **Stemmer leve** (`aprovacao`/`aprovar`/`aprovados` → `aprov`): sem ele a busca
  por "aprovar pagamento" não encontra "limite de aprovação de pagamentos".
* **Stopwords**: sem elas uma palavra banal ("de", "para") domina o cosseno e o
  espaço vetorial deixa de discriminar — medido: textos sem relação chegavam a
  0,69 de similaridade.
* **Char 4-gramas** com peso baixo (0,1): toleram erro de digitação; com peso
  alto inflam a similaridade de qualquer par de frases em português.

Evolução: o campo `embedding_model` identifica algoritmo e dimensão. Trocar de
backend (ex.: um modelo de embeddings real) é gerar novo `model_id` e rodar
`egr memory reindex` — o acervo antigo continua respondendo pelo lado léxico.

## 2. Recuperação híbrida (ADR-022)

```bash
egr memory search "liberar pagamento sem aprovação" -n finance --explain
```

| Modo | Quando usar |
|---|---|
| `hybrid` (padrão) | melhor cobertura: léxico + semântico |
| `fts` | identificadores exatos (NF 82731, CNPJ, códigos) |
| `semantic` | vocabulário diferente, sinônimos, erro de digitação |

A fusão é **RRF ponderado** (`memory.fts_weight`, `memory.semantic_weight`):

```
score = w_fts / (60 + rank_fts) + w_sem / (60 + rank_sem)
```

RRF evita o problema de somar escalas incomparáveis (BM25 é ilimitado e
negativo; cosseno vai de −1 a 1) e degrada bem quando um dos lados não traz
candidato nenhum.

```yaml
memory:
  retrieval: hybrid        # hybrid | fts | semantic
  fts_weight: 1.0
  semantic_weight: 1.0
  candidate_multiplier: 4
  min_cosine: 0.12         # corte do lado semântico
  max_semantic_scan: 20000 # acima disso, varre candidatos do FTS + recentes
```

## 3. Ciclo de vida (ADR-023)

```
importância (tipo + sinais de norma)
    × reforço      (1 + log(1 + acessos))
    × decaimento   (0.5 ^ (idade_dias / meia_vida))
  = saliência      → viva · estável · esfriando · arquivável
```

* **Reforço**: cada recall incrementa `access_count` e `last_accessed_at` —
  memória usada fica mais forte, e isso é persistido.
* **Decaimento**: meia-vida de 30 dias (`memory.half_life_days`). Nada é apagado
  por decaimento: a memória só *esfria*.
* **Consolidação**: `egr memory consolidate` detecta near-duplicatas
  (cosseno ≥ `duplicate_threshold`, padrão 0,90), **arquiva a de menor
  saliência** e aponta `duplicate_of` para a sobrevivente. Sem `--apply` nada
  muda — é relatório.
* **Poda**: `--prune` remove arquivados com retenção vencida
  (`retention_days`), e só com `--apply`.

```bash
egr memory consolidate                 # relatório
egr memory consolidate --apply         # arquiva duplicatas
egr memory consolidate --prune --apply # também remove o que venceu
egr memory reindex                     # reconstrói vetores
egr memory forget <id>                 # remove de verdade (registro+FTS+vetor)
egr memory stats                       # tipos, namespaces, vetores, saliência
```

Arquivados saem da busca padrão (`--archived` traz de volta). A busca também
**auto-cura** registros sem vetor, e o `egr doctor` avisa quando existem
registros sem vetor (`memory:vetores`).

## 4. Correção incluída: índice FTS duplicado

`MemoryRepository.save` reinseria no índice FTS5 sem remover a linha anterior.
Como a FTS não tem chave primária, cada reforço de saliência (que reescreve o
registro) duplicava o item na busca. Agora o `save` limpa a linha antes de
reinserir.

## Próximo passo (Fase 6)

Orchestration: scheduler, triggers (cron/webhook/db), sub-tasks, retry,
compensação e coordenação multi-agente.
