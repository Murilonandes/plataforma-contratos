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

from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Final

BRL = Decimal("0.01")
QTY = Decimal("0.001")
PCT = Decimal("0.0001")
RATE = Decimal("0.000000001")

# Contexto explicito do dominio: nenhuma operacao depende do contexto decimal da
# thread (``getcontext()``). 50 digitos cobre com folga o maior valor do servico
# (Precision 23) e as contas intermediarias; estouro e erro, nunca arredondamento.
CONTEXTO_DECIMAL: Final = Context(
    prec=50,
    rounding=ROUND_HALF_UP,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


def _ensure_decimal(v: object) -> Decimal:
    """Garante ``Decimal`` finito; ``float``/``int``/``str`` e NaN/Infinity sao erro."""
    if not isinstance(v, Decimal):
        raise TypeError("use Decimal, nunca float")
    if not v.is_finite():
        raise ValueError("valor monetario/quantidade precisa ser finito (sem NaN/Infinity)")
    return v


def casas_decimais(v: Decimal) -> int:
    """Casas decimais significativas: zeros a direita nao contam; zero tem 0 casas.

    Regra unica de escala do dominio e do mapper. ``normalize`` num contexto com
    precisao igual ao numero de digitos do proprio valor: exato, nunca arredonda,
    e nao depende do contexto decimal da thread.
    """
    exato = Context(prec=len(_ensure_decimal(v).as_tuple().digits))
    return max(0, -int(v.normalize(exato).as_tuple().exponent))


def sem_zero_negativo(v: Decimal) -> Decimal:
    """``-0`` (em qualquer expoente) vira ``0``; os demais valores ficam intactos."""
    return v.copy_abs() if v.is_zero() else v


def _quantize(v: Decimal, quantum: Decimal) -> Decimal:
    with localcontext(CONTEXTO_DECIMAL):
        return _ensure_decimal(v).quantize(quantum, rounding=ROUND_HALF_UP)


def quantize_brl(v: Decimal) -> Decimal:
    return _quantize(v, BRL)


def quantize_qty(v: Decimal) -> Decimal:
    return _quantize(v, QTY)


def quantize_pct(v: Decimal) -> Decimal:
    return _quantize(v, PCT)


def quantize_rate(v: Decimal) -> Decimal:
    return _quantize(v, RATE)
