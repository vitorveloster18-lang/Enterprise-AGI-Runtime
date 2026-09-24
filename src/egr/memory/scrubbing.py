"""Lacuna 5b: limpeza de PII na escrita — a memória não é cofre de dado pessoal.

Memória de empresa lembra **o que aconteceu e o que foi decidido**, não o CPF de
ninguém. Escrever dado pessoal num acervo que é consultado por padrão (e copiado
para contexto de modelo) é o jeito mais rápido de vazar o que não devia.

Como funciona:

- a limpeza acontece **na escrita**, antes de qualquer coisa (antes do
  embedding e antes do banco): o dado pessoal nunca chega a ser persistido;
- o que sai é substituído por um marcador (`[cpf removido]`), para que o texto
  continue legível e a lacuna seja óbvia para quem lê;
- o que foi removido é **registrado por tipo e contagem** — nunca o valor.
  Ninguém reconstrói o CPF lendo a trilha de auditoria;
- é configurável por workspace: `memory.scrub_pii` desliga, `memory.pii_allow`
  libera um tipo específico (ex.: e-mail corporativo).

Reconhecidos: CPF, CNPJ, e-mail, telefone (BR e internacional simples), cartão
de crédito (com Luhn), CEP e RG no formato comum.
"""

from __future__ import annotations

import re
from typing import Any

MARK = "[{} removido]"


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _luhn(value: str) -> bool:
    digits = _digits(value)
    if not 13 <= len(digits) <= 19:
        return False
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _repeated(digits: str) -> bool:
    return len(set(digits)) <= 1


def _valid_cpf(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 11 or _repeated(digits):
        return False
    for size in (9, 10):
        total = sum(int(digits[index]) * (size + 1 - index) for index in range(size))
        check = (total * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


def _valid_cnpj(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 14 or _repeated(digits):
        return False
    for size in (12, 13):
        weight = 5 if size == 12 else 6
        total = 0
        for index in range(size):
            total += int(digits[index]) * weight
            weight -= 1
            if weight == 1:
                weight = 9
        check = 11 - total % 11
        if check >= 10:
            check = 0
        if check != int(digits[size]):
            return False
    return True


#: nome -> (expressão, validador opcional)
PATTERNS: dict[str, tuple[re.Pattern[str], Any]] = {
    "cpf": (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), _valid_cpf),
    "cnpj": (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), _valid_cnpj),
    "cartão": (re.compile(r"\b(?:\d[ -]?){13,19}\b"), _luhn),
    "e-mail": (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"), None),
    "telefone": (
        re.compile(r"(?<![\w.])(?:\+\d{2}[ -]?)?(?:\(\d{2}\)[ -]?)?\d{4,5}[ -]?\d{4}(?![\w.])"),
        None,
    ),
    "cep": (re.compile(r"\b\d{5}-?\d{3}\b"), None),
    "rg": (re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-?[\dxX]\b"), None),
}

#: ordem importa: cartão antes de telefone, cnpj antes de cpf (dígitos maiores)
ORDER = ("cnpj", "cpf", "cartão", "e-mail", "cep", "rg", "telefone")


def scan(text: str, *, allowed: list[str] | tuple[str, ...] = ()) -> dict[str, int]:
    """Quantas ocorrências de cada tipo existem (sem revelar valor nenhum)."""

    counts: dict[str, int] = {}
    for name in ORDER:
        if name in allowed:
            continue
        pattern, validate = PATTERNS[name]
        hits = 0
        for match in pattern.finditer(text):
            value = match.group(0)
            if validate is not None and not validate(value):
                continue
            hits += 1
        if hits:
            counts[name] = hits
    return counts


def scrub(text: str, *, allowed: list[str] | tuple[str, ...] = ()) -> tuple[str, dict[str, int]]:
    """Remove o que é pessoal e devolve (texto limpo, {tipo: ocorrências})."""

    if not text:
        return text, {}
    cleaned = text
    removed: dict[str, int] = {}
    for name in ORDER:
        if name in allowed:
            continue
        pattern, validate = PATTERNS[name]
        hits = 0

        def replace(match: re.Match[str], _name: str = name, _validate=validate) -> str:
            nonlocal hits
            value = match.group(0)
            if _validate is not None and not _validate(value):
                return value
            hits += 1
            return MARK.format(_name)

        cleaned = pattern.sub(replace, cleaned)
        if hits:
            removed[name] = hits
    return cleaned, removed


def summarize(removed: dict[str, int]) -> str:
    """Frase curta para trilha e CLI: '2 cpf, 1 e-mail removidos'."""

    if not removed:
        return ""
    parts = [f"{count} {name}" for name, count in sorted(removed.items())]
    return ", ".join(parts) + (" removido" if sum(removed.values()) == 1 else " removidos")


__all__ = ["ORDER", "PATTERNS", "scan", "scrub", "summarize"]
