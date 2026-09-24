"""Regra unica de caracteres invalidos em texto do dominio.

Recusa caractere de controle (categoria Unicode ``Cc``: C0, DEL e C1) e
surrogate isolado (``Cs``). ``\\t``, ``\\n`` e ``\\r`` so em texto multilinha
(``LongText``, justificativa). Motivos: o Postgres recusa ``\\x00`` em ``text``,
surrogate isolado vira JSON que muitos parsers rejeitam, e controle nao tem
lugar em campo do SAP.
"""

from __future__ import annotations

import unicodedata
from typing import Final

_NENHUM: Final = frozenset[str]()
_QUEBRAS: Final = frozenset("\t\n\r")


def invalido_em_linha(texto: str) -> bool:
    """Texto de uma linha: nenhum caractere de controle."""
    return any(_invalido(ch, _NENHUM) for ch in texto)


def invalido_em_multilinha(texto: str) -> bool:
    """Texto livre: controle so ``\\t``, ``\\n`` e ``\\r``."""
    return any(_invalido(ch, _QUEBRAS) for ch in texto)


def _invalido(ch: str, permitidos: frozenset[str]) -> bool:
    categoria = unicodedata.category(ch)
    if categoria == "Cs":
        return True
    return categoria == "Cc" and ch not in permitidos
