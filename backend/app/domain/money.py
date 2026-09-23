"""Quantizacao de dinheiro (BRL), quantidade e percentual.

Regra do projeto: ``Decimal`` sempre, nunca ``float`` (CLAUDE.md). Arredondamento
``ROUND_HALF_UP`` (meio afasta do zero), explicito em cada chamada: o default do
modulo ``decimal`` e ``ROUND_HALF_EVEN`` e daria outro resultado nos empates.

- ``BRL`` 2 casas (``Valor`` das parcelas, precos)
- ``QTY`` 3 casas (``RequestedQuantity``)
- ``PCT`` 4 casas (``Porcentagem`` das parcelas)
- ``RATE`` 9 casas (``ConditionRateValue``, Edm.Decimal Scale 9)
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

BRL = Decimal("0.01")
QTY = Decimal("0.001")
PCT = Decimal("0.0001")
RATE = Decimal("0.000000001")


def _ensure_decimal(v: object) -> Decimal:
    """Garante ``Decimal`` finito; ``float``/``int``/``str`` e NaN/Infinity sao erro."""
    if not isinstance(v, Decimal):
        raise TypeError("use Decimal, nunca float")
    if not v.is_finite():
        raise ValueError("valor monetario/quantidade precisa ser finito (sem NaN/Infinity)")
    return v


def quantize_brl(v: Decimal) -> Decimal:
    return _ensure_decimal(v).quantize(BRL, rounding=ROUND_HALF_UP)


def quantize_qty(v: Decimal) -> Decimal:
    return _ensure_decimal(v).quantize(QTY, rounding=ROUND_HALF_UP)


def quantize_pct(v: Decimal) -> Decimal:
    return _ensure_decimal(v).quantize(PCT, rounding=ROUND_HALF_UP)


def quantize_rate(v: Decimal) -> Decimal:
    return _ensure_decimal(v).quantize(RATE, rounding=ROUND_HALF_UP)
