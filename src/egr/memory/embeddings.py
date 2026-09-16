"""Embedding determinístico local — sem dependências, sem rede, sem download.

A memória semântica do EGR não pode depender de um serviço de embeddings: a
empresa é o dono dos dados e o Runtime precisa funcionar offline (ADR-021).

O que este módulo implementa é **feature hashing** (hashing trick) sobre três
famílias de características:

    palavras        peso 1.00  -> vocabulário
    bigramas        peso 0.50  -> contexto curto ("limite de", "aprovação automática")
    char 4-gramas   peso 0.25  -> morfologia e tolerância a erro de digitação

Cada característica é projetada em um índice do vetor por `blake2b` (estável
entre processos e versões — ao contrário de `hash()` do Python) com sinal
aleatório, o que preserva o produto interno em expectativa. O vetor final é
normalizado (L2), então a similaridade é simplesmente o cosseno.

Onde isso é suficiente: recuperação por tópico, tolerância a variação de
forma, detecção de near-duplicatas e agrupamento.

Onde **não** é: sinônimos distantes e raciocínio semântico profundo — para isso
existe o lado léxico (BM25) e, no futuro, um backend de embeddings plugável
(`EMBEDDINGS: dict` versionado por `model_id`, pronto para troca).
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from itertools import pairwise

DIMENSION = 512
MODEL_ID = f"egr-hash-{DIMENSION}"

WORD_WEIGHT = 1.0
BIGRAM_WEIGHT = 0.5
#: char n-gramas toleram variação e erro de digitação, mas são "ruidosos":
#  em português quase qualquer par de frases compartilha sequências de letras.
#  Com peso alto eles inflam o cosseno de textos sem relação alguma.
CHARGRAM_WEIGHT = 0.1
CHARGRAM_SIZE = 4
MAX_CHARS = 4000

#: Stopwords PT-BR. Sem elas, uma palavra banal ("de", "para") presente em quase
#: todo documento domina o cosseno e destrói a discriminação do espaço vetorial.
_STOPWORDS_TEXT = """
de a o os as ao aos à às da do das dos dum duma num numa no na nos nas em um uma uns umas
e ou mas porque que se como quando onde qual quais quem cujo cuja
ele ela eles elas isso esse essa esses essas este esta estes estas aquele aquela aqueles aquelas
eu tu voce voce ele nos vos eles meu minha meus minhas seu sua seus suas nosso nossa nossos nossas
foi era foram ser sendo somos sao foi fui foi ter tem temos tive teve tiveram havemos ha houveram
muito muita muitos muitas pouco pouca mais menos talvez nao sim tambem ja ainda sempre nunca
para por perto desde ate sobre sob entre com sem contra conforme segundo mediante
"""

STOPWORDS = frozenset(_STOPWORDS_TEXT.split())

_WORD_PATTERN = re.compile(r"[\w]+", re.UNICODE)

#: Stemmer leve de português (sufixos, semradão): "aprovacao"/"aprovar"/"aprovados"
#: precisam cair na mesma característica — sem isso a memória semântica não acha
#: "aprovar pagamento" em "limite de aprovação de pagamentos".
_SUFFIXES = (
    "íssimo", "issimo", "mente",
    "ções", "coes", "ção", "cao", "são", "sao",
    "amento", "imento", "amento", "idade", "idades",
    "ante", "antes", "ável", "avel", "ível", "ivel",
    "ismo", "ista", "istas",
    "ados", "adas", "idos", "idas", "ado", "ada", "ido", "ida",
    "ando", "endo", "indo",
    "ais", "eis", "óis", "ois", "ães", "aes", "ões", "oes",
    "ans", "ens", "ins", "ons", "uns",
    "ar", "er", "ir", "or", "as", "es", "is", "os", "us", "ns", "ão",
    "a", "e", "o",
)
#: radical mínimo aceito: abaixo disso o sufixo não é cortado
_MIN_STEM = 3
#: duas passadas: "aprovacao" -> "aprova" -> "aprov"
_STEM_PASSES = 2


def normalize(text: str) -> str:
    """Caixa baixa, sem acentos, só o que importa para casar termos em PT-BR."""

    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def stem(word: str) -> str:
    """Radical aproximado (PT-BR). Não é um stemmer linguisticamente puro —
    é determinístico, curto e suficiente para casar flexões comuns.
    """

    result = word
    for _ in range(_STEM_PASSES):
        for suffix in _SUFFIXES:
            if result.endswith(suffix) and len(result) - len(suffix) >= _MIN_STEM:
                result = result[: -len(suffix)]
                break
        else:
            break
    if len(result) > _MIN_STEM and result.endswith("s"):
        result = result[:-1]
    return result


def stems(text: str) -> list[str]:
    return [stem(token) for token in tokenize(text)]


def tokenize(text: str) -> list[str]:
    """Palavras de conteúdo: sem stopwords e sem token de 1 caractere."""

    return [
        token
        for token in _WORD_PATTERN.findall(normalize(text))
        if len(token) > 1 and token not in STOPWORDS
    ]


def features(text: str) -> list[tuple[str, float]]:
    """(característica, peso) — a unidade que vira dimensão do vetor."""

    words = tokenize(text)
    if not words:
        return []
    radicals = [stem(word) for word in words]
    items: list[tuple[str, float]] = [(radical, WORD_WEIGHT) for radical in radicals]
    items.extend((f"{a}_{b}", BIGRAM_WEIGHT) for a, b in pairwise(radicals))

    compact = "".join(radicals)[:MAX_CHARS]
    items.extend(
        (compact[index : index + CHARGRAM_SIZE], CHARGRAM_WEIGHT)
        for index in range(max(0, len(compact) - CHARGRAM_SIZE + 1))
    )
    return items


def embed(text: str, dimension: int = DIMENSION) -> list[float]:
    """Vetor L2-normalizado e determinístico. Texto vazio => vetor nulo."""

    vector = [0.0] * dimension
    counts: dict[str, int] = {}
    for token, _weight in features(text):
        counts[token] = counts.get(token, 0) + 1

    for token, weight in features(text):
        digest = hashlib.blake2b(f"{MODEL_ID}|{token}".encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % dimension
        # tf sublinear: repetir a palavra 10x não deve valer 10x
        vector[index] += weight * (1.0 + math.log(counts[token])) * (1.0 if value & 1 else -1.0)

    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return vector
    return [component / norm for component in vector]


def cosine(left: list[float], right: list[float]) -> float:
    """Similaridade do cosseno (vetores já normalizados => produto interno)."""

    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=False))


def to_bytes(vector: list[float]) -> bytes:
    import array

    return array.array("f", vector).tobytes()


def from_bytes(raw: bytes) -> list[float]:
    import array

    values = array.array("f")
    values.frombytes(raw)
    return list(values)


__all__ = [
    "DIMENSION",
    "MODEL_ID",
    "cosine",
    "embed",
    "features",
    "from_bytes",
    "normalize",
    "stem",
    "stems",
    "to_bytes",
    "tokenize",
]
