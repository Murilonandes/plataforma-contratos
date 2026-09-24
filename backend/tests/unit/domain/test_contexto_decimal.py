"""Nenhuma funcao do dominio depende do contexto decimal da thread (B2).

Cada chamada roda sob um contexto HOSTIL (precisao 3, ``ROUND_FLOOR``, traps em
``Inexact``/``Rounded``): se alguma operacao usasse o contexto global, o
resultado mudaria ou uma excecao escaparia. O resultado tem que ser identico
ao obtido no contexto padrao.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, Inexact, Rounded, localcontext
from typing import Any

import pytest

from app.domain.contract import Contract
from app.domain.errors import DomainValidationError
from app.domain.installments import calcular_parcelas
from app.domain.money import (
    CONTEXTO_DECIMAL,
    casas_decimais,
    quantize_brl,
    quantize_pct,
    quantize_qty,
    quantize_rate,
)
from app.domain.rules import validar_parcelas
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada


@contextmanager
def contexto_hostil() -> Iterator[None]:
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = ROUND_FLOOR
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        yield


def _mesmo_resultado(f: Callable[[], Any]) -> None:
    esperado = f()
    with contexto_hostil():
        assert f() == esperado


def test_contexto_do_dominio_e_explicito() -> None:
    assert CONTEXTO_DECIMAL.prec == 50
    assert CONTEXTO_DECIMAL.rounding == ROUND_HALF_UP


@pytest.mark.parametrize(
    ("f", "valor"),
    [
        (quantize_brl, "12345678901234.565"),
        (quantize_qty, "123456789012.3456"),
        (quantize_pct, "99999999999.99995"),
        (quantize_rate, "12345678901234.1234567895"),
    ],
)
def test_quantize_nao_depende_do_contexto(f: Callable[[Decimal], Decimal], valor: str) -> None:
    _mesmo_resultado(lambda: f(Decimal(valor)))


def test_quantize_arredonda_meio_para_cima_mesmo_com_contexto_floor() -> None:
    with contexto_hostil():
        assert quantize_brl(Decimal("10.005")) == Decimal("10.01")
        assert quantize_brl(Decimal("-10.005")) == Decimal("-10.01")


def test_casas_decimais_nao_depende_do_contexto() -> None:
    _mesmo_resultado(lambda: casas_decimais(Decimal("1." + "0" * 30 + "1")))


def test_calcular_parcelas_nao_depende_do_contexto() -> None:
    datas = [date(2026, 1, 1) + timedelta(days=30 * i) for i in range(7)]
    _mesmo_resultado(
        lambda: calcular_parcelas(Decimal("9999999999999.99"), [1, 2, 3, 4, 5, 6, 10000], datas)
    )


def test_minimo_total_nao_depende_do_contexto() -> None:
    datas = [date(2026, 1, 1), date(2026, 2, 1)]
    with pytest.raises(DomainValidationError) as normal:
        calcular_parcelas(Decimal("0.01"), [1, 9999], datas)
    with contexto_hostil(), pytest.raises(DomainValidationError) as hostil:
        calcular_parcelas(Decimal("0.01"), [1, 9999], datas)
    assert str(hostil.value) == str(normal.value)


def test_soma_das_porcentagens_nao_depende_do_contexto() -> None:
    parcelas = [
        (f"to_FormPag[{i}]", i + 1, Decimal(p), None)
        for i, p in enumerate(["33.3334", "33.3333", "33.3332"])
    ]
    _mesmo_resultado(lambda: validar_parcelas(parcelas, recebidas=3))
    assert validar_parcelas(parcelas, recebidas=3)[0].params["soma"] == "99.9999"


def test_contract_criar_nao_depende_do_contexto() -> None:
    dados = payload_exemplo_como_entrada()
    dados["to_PricingElement"][0]["ConditionRateValue"] = Decimal("12345678901234.123456789")
    dados["to_Item"][0]["RequestedQuantity"] = Decimal("123456789012.345")
    _mesmo_resultado(lambda: Contract.criar(dados))
