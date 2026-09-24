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

_QUEBRAS_PERMITIDAS: Final = frozenset("\t\n\r")


def tem_caractere_invalido(texto: str, *, multilinha: bool) -> bool:
    return any(_invalido(ch, multilinha=multilinha) for ch in texto)


def _invalido(ch: str, *, multilinha: bool) -> bool:
    categoria = unicodedata.category(ch)
    if categoria == "Cs":
        return True
    return categoria == "Cc" and not (multilinha and ch in _QUEBRAS_PERMITIDAS)
