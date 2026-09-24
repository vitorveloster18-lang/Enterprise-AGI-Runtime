# Rodando local: Ollama + hardware

O EGR foi desenhado para terminar **dentro da empresa**: os dados não precisam
sair da máquina. O caminho recomendado é híbrido — nuvem no piloto, local nos
executoras, tudo local quando o hardware chegar — e o Runtime já suporta as
três fases sem mudar agente nenhum (só o `egr.yaml`).

> Tamanhos e janelas abaixo são aproximados (quantização Q4, padrão do Ollama)
> e mudam com o tempo. Valores atuais: `ollama show <modelo>` e
> <https://ollama.com/library>. Guia escrito em set/2026.

## 1. Instalar o Ollama

Linux:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
```

macOS e Windows: instalador em <https://ollama.com/download>. O servidor sobe
em `http://localhost:11434` — o endereço que o EGR já espera por padrão.

Baixar os modelos (uma vez por máquina):

```bash
ollama pull llama3.1:8b      # executoras: barato e rápido
ollama pull qwen2.5:14b      # orquestrador/revisão: raciocina melhor
ollama list                  # confere o que está em disco
```

## 2. Modelos e papéis

| Papel no EGR | Modelo sugerido | Disco aprox. | Janela típica |
|---|---|---|---|
| Executoras de área | `llama3.1:8b`, `qwen2.5:7b` | ~5 GB | 32k–128k |
| Orquestrador / revisão | `qwen2.5:14b`, `qwen2.5:32b` | ~9–20 GB | 32k–128k |
| Raciocínio pesado | `deepseek-r1:14b`, `deepseek-r1:32b` | ~9–20 GB | 64k–128k |

O orquestrador merece o modelo maior: ele planeja, revisa entregas e decide
escalações. Executoras se viram bem com 8b — elas seguem planos, não criam.

**Pegadinha do Ollama:** a janela efetiva padrão é `num_ctx=4096`, mesmo que o
modelo suporte mais. Para conversas longas, suba no `egr.yaml` (vai direto
para a API do Ollama):

```yaml
options:
  num_ctx: 16384
```

E declare a mesma janela em `max_context_tokens` para a política de overflow
saber onde cortar/escalar (fatia 6).

## 3. Hardware: quanto basta?

| Cenário | RAM/VRAM | O que roda |
|---|---|---|
| CPU, máquina comum | 16 GB RAM | 8b lento, mas funciona (piloto) |
| GPU 8–12 GB (ex.: RTX 3060/4060) | 12 GB VRAM | 8b folgado, 14b confortável |
| GPU 24 GB (ex.: RTX 4090) / Mac 32 GB | 24 GB | 32b, orquestrador + executoras juntos |
| Servidor da empresa | 64 GB+ / 2 GPUs | tudo local, todos os departamentos |

Regra de bolso: **RAM livre ≈ 1,3× o tamanho do modelo**. Quantização menor
(`:8b` já é Q4) troca qualidade por memória — para o piloto, não mexa nisso.

Apple Silicon (M1+) funciona bem: a memória unificada serve de VRAM, então um
Mac com 32 GB roda 14b com folga.

## 4. Configurar o EGR

Exemplo híbrido (`egr.yaml`): local primeiro, nuvem como transbordo — e o
transbordo de graça via `overflow: escalate`:

```yaml
models:
  routing: local_first
  overflow: escalate
  providers:
    - name: local-exec
      type: ollama
      model: llama3.1:8b
      base_url: http://localhost:11434
      priority: 10
      max_context_tokens: 16384
      options:
        num_ctx: 16384
    - name: cloud-reasoning
      type: openai_compat
      model: gemini-2.5-flash
      base_url: https://generativelanguage.googleapis.com/v1beta/openai
      api_key_env: GEMINI_API_KEY
      external: true
      priority: 1
      max_context_tokens: 1000000
```

Para **tudo local** (fase final): remova o provider de nuvem e trave a
fronteira no `egr.yaml`:

```yaml
enterprise:
  settings:
    external_ai: forbidden
```

Qualquer tentativa de sair da máquina vira negação auditada, não vazamento.

## 5. Validar

```bash
egr models health            # Ollama de pé? quais modelos?
egr models test --provider local-exec --prompt "Responda apenas: OK"
egr audit show --type model.called --limit 5   # chamadas na trilha
```

## 6. Problemas comuns

| Sintoma | Causa provável | Ação |
|---|---|---|
| `unreachable at http://localhost:11434` | Ollama parado | `ollama serve` (ou o app no macOS/Win) |
| `model not found` | esqueceu o pull | `ollama pull <modelo>` |
| Resposta lenta no CPU | esperado em 8b+ sem GPU | piloto ok; GPU na fase 2 |
| `model.overflow` com `truncated` | conversa maior que `num_ctx` | subir `num_ctx`/`max_context_tokens`, ou deixar escalar |
| `ContextOverflow` | política `deny` + janela curta | trocar para `escalate`, ou aumentar a janela |

Toda decisão de overflow cai na trilha (`model.overflow`, com política, ação e
tamanhos) — o pacote de evidência da fatia 4 cobre o gateway sem código novo.
