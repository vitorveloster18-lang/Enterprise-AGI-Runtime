"""Lacuna 8b: medir **qualidade**, não só encanamento.

A Fase 8 prova que o artefato funciona (passa, custa pouco, é rápido). O que
faltava era a pergunta empresarial: **a resposta presta?**

Dois caminhos, na mesma balança de 0 a 1:

- **similaridade** — determinística, roda offline, custa zero e é auditável:
  metade do peso é sobreposição de vocabulário (Jaccard), metade é conter os
  trechos que o caso declarou como obrigatórios;
- **modelo** — pede a um provedor que julgue a resposta contra o esperado.
  Custa, chama sistema externo e pode falhar: quando falha (ou quando não há
  provedor), a nota cai para a similaridade **com `degradado=True` dito no
  relatório**. Laboratório que esconde degradação não serve para decidir.
"""

from __future__ import annotations

import re
from typing import Any

from ..domain.evaluation import EvaluationCase, QualityScore

STOPWORDS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos", "e", "em", "entre",
    "era", "essa", "esse", "esta", "este", "eu", "foi", "fora", "ha", "isso", "ja", "lhe", "mais",
    "mas", "me", "mesmo", "meu", "muito", "na", "nas", "no", "nos", "nossa", "nosso", "num", "numa",
    "o", "os", "ou", "para", "pela", "pelas", "pelo", "pelos", "por", "qual", "quando", "que", "se",
    "sem", "seu", "seus", "so", "sua", "suas", "tambem", "te", "tem", "um", "uma", "umas", "uns",
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
}

PROMPT = (
    "Você é um avaliador rigoroso. Responda SOMENTE uma linha no formato:\n"
    "NOTA: <número de 0 a 1> | MOTIVO: <uma frase curta>\n\n"
    "Pergunta/esperado: {expected}\n"
    "Resposta avaliada: {answer}\n"
)


def tokens(text: str) -> set[str]:
    """Vocabulário normalizado: minúsculas, sem pontuação, sem palavra vazia."""

    words = re.findall(r"[\wÀ-ÿ]+", (text or "").lower())
    return {word for word in words if word not in STOPWORDS and len(word) > 1}


def similarity(answer: str, expected: str, *, required: list[str] | None = None) -> tuple[float, str]:
    """Nota determinística: metade sobreposição, metade trechos obrigatórios.

    Sem `expected`, só os trechos obrigatórios (se houver) contam — e a
    sobreposição vale zero, porque não existe referência para comparar.
    """

    answer_text = (answer or "").strip()
    expected_text = (expected or "").strip()
    required = required or []
    low = answer_text.lower()

    hits = [term for term in required if term.lower() in low]
    coverage = (len(hits) / len(required)) if required else 0.0

    expected_tokens, answer_tokens = tokens(expected_text), tokens(answer_text)
    union = expected_tokens | answer_tokens
    overlap = (len(expected_tokens & answer_tokens) / len(union)) if union else 0.0

    if not expected_text:
        score = coverage
        reason = f"trechos obrigatórios: {len(hits)}/{len(required)}" if required else "sem referência declarada"
        return round(max(0.0, min(1.0, score)), 3), reason

    score = (overlap * 0.5) + (coverage * 0.5)
    reason = f"sobreposição {overlap:.2f} · trechos {len(hits)}/{len(required)}" if required else (
        f"sobreposição {overlap:.2f}"
    )
    return round(max(0.0, min(1.0, score)), 3), reason


def model_judge(runtime: Any, answer: str, expected: str) -> tuple[float, str, float]:
    """Julgamento por provedor. Levanta exceção quando não dá — quem chama decide."""

    from ..models.gateway import CompletionRequest, Message

    request = CompletionRequest(
        messages=[
            Message(role="system", content="Avaliador de qualidade. Sempre responda no formato pedido."),
            Message(role="user", content=PROMPT.format(expected=expected, answer=answer)),
        ],
        capability="reasoning",
        max_tokens=120,
    )
    response = runtime.gateway.complete(request, task_id=None, environment=str(runtime.settings.environment))
    text = (response.text or "").strip()
    match = re.search(r"NOTA:\s*([0-9]*[.,]?[0-9]+)", text)
    reason = ""
    if "MOTIVO:" in text:
        reason = text.split("MOTIVO:", 1)[1].strip()[:200]
    if not match:
        raise ValueError(f"juiz não devolveu nota no formato pedido: {text[:120]!r}")
    score = float(match.group(1).replace(",", "."))
    return max(0.0, min(1.0, score)), reason or "juiz de modelo", float(response.cost or 0.0)


def judge_case(
    runtime: Any,
    case: EvaluationCase,
    answer: str,
    *,
    method: str = "auto",
) -> QualityScore:
    """Nota de um caso — com degradação declarada, nunca escondida."""

    expected = case.expected or ""
    required = list(case.expected_contains or [])
    base_score, base_reason = similarity(answer, expected, required=required)

    if method == "similaridade":
        return QualityScore(case_id=case.id, method="similaridade", score=base_score, reason=base_reason)

    if method in ("auto", "modelo"):
        try:
            score, reason, cost = model_judge(runtime, answer, expected or " ".join(required) or case.description)
        except Exception as exc:  # provedor fora do ar, sem provedor, resposta fora do formato
            if method == "modelo":
                return QualityScore(
                    case_id=case.id,
                    method="similaridade",
                    score=base_score,
                    reason=f"juiz indisponível ({exc}); caiu para similaridade",
                    degraded=True,
                )
            return QualityScore(
                case_id=case.id,
                method="similaridade",
                score=base_score,
                reason=base_reason + " · juiz de modelo indisponível",
                degraded=True,
            )
        return QualityScore(case_id=case.id, method="modelo", score=score, reason=reason, cost=cost)

    raise ValueError(f"método de avaliação desconhecido: {method}")


def aggregate(scores: list[QualityScore]) -> dict[str, Any]:
    """Métricas de qualidade de uma rodada (a suíte inteira)."""

    if not scores:
        return {
            "casos": 0,
            "qualidade_média": 0.0,
            "mínima": 0.0,
            "máxima": 0.0,
            "método": "-",
            "degradados": 0,
            "custo": 0.0,
        }
    values = [item.score for item in scores]
    methods = {item.method for item in scores}
    return {
        "casos": len(scores),
        "qualidade_média": round(sum(values) / len(values), 4),
        "mínima": round(min(values), 4),
        "máxima": round(max(values), 4),
        "método": "modelo" if "modelo" in methods else "similaridade",
        "degradados": sum(1 for item in scores if item.degraded),
        "custo": round(sum(item.cost for item in scores), 8),
    }


__all__ = ["PROMPT", "STOPWORDS", "aggregate", "judge_case", "model_judge", "similarity", "tokens"]
