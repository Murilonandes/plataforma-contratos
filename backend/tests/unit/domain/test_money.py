"""Testes de ``app.domain.money`` (quantizacao de BRL, quantidade e percentual).

Regra: ``Decimal`` sempre, arredondamento ``ROUND_HALF_UP``. Os empates usam um
digito anterior PAR (10.005, 1.2345, 33.33325) para distinguir HALF_UP de
HALF_EVEN (o default do ``decimal``): um arredondamento trocado nao passa.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.money import BRL, PCT, QTY, quantize_brl, quantize_pct, quantize_qty

# ---- Exemplos do plano -------------------------------------------------------


def test_brl_empate_arredonda_para_cima() -> None:
    assert quantize_brl(Decimal("10.005")) == Decimal("10.01")


def test_brl_abaixo_do_meio_arredonda_para_baixo() -> None:
    assert quantize_brl(Decimal("10.004")) == Decimal("10.00")


def test_qty_tres_casas() -> None:
    assert quantize_qty(Decimal("1.23456")) == Decimal("1.235")


def test_pct_quatro_casas() -> None:
    assert quantize_pct(Decimal("33.33335")) == Decimal("33.3334")


# ---- HALF_UP (nao HALF_EVEN) em cada unidade ---------------------------------


@pytest.mark.parametrize(
    ("funcao", "entrada", "esperado"),
    [
        (quantize_brl, "10.005", "10.01"),  # HALF_EVEN daria 10.00
        (quantize_brl, "0.125", "0.13"),  # HALF_EVEN daria 0.12
        (quantize_qty, "1.2345", "1.235"),  # HALF_EVEN daria 1.234
        (quantize_qty, "0.0005", "0.001"),  # HALF_EVEN daria 0.000
        (quantize_pct, "33.33325", "33.3333"),  # HALF_EVEN daria 33.3332
        (quantize_pct, "0.00005", "0.0001"),  # HALF_EVEN daria 0.0000
    ],
)
def test_empate_usa_half_up(funcao: object, entrada: str, esperado: str) -> None:
    assert funcao(Decimal(entrada)) == Decimal(esperado)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("funcao", "entrada", "esperado"),
    [
        (quantize_brl, "-10.005", "-10.01"),  # HALF_UP afasta do zero
        (quantize_qty, "-1.2345", "-1.235"),
        (quantize_pct, "-33.33325", "-33.3333"),
    ],
)
def test_negativo_arredonda_afastando_do_zero(funcao: object, entrada: str, esperado: str) -> None:
    assert funcao(Decimal(entrada)) == Decimal(esperado)  # type: ignore[operator]


# ---- Numero de casas no resultado --------------------------------------------


@pytest.mark.parametrize(
    ("funcao", "unidade", "expoente"),
    [(quantize_brl, BRL, -2), (quantize_qty, QTY, -3), (quantize_pct, PCT, -4)],
)
def test_resultado_tem_exatamente_as_casas_da_unidade(
    funcao: object, unidade: Decimal, expoente: int
) -> None:
    assert unidade.as_tuple().exponent == expoente
    resultado = funcao(Decimal("7"))  # type: ignore[operator]
    assert resultado.as_tuple().exponent == expoente
    assert str(resultado) == "7." + "0" * -expoente


def test_constantes_das_unidades() -> None:
    assert Decimal("0.01") == BRL
    assert Decimal("0.001") == QTY
    assert Decimal("0.0001") == PCT


# ---- Rejeita o que nao e Decimal ---------------------------------------------


@pytest.mark.parametrize("funcao", [quantize_brl, quantize_qty, quantize_pct])
@pytest.mark.parametrize("valor", [10.005, 10, "10.005", None])
def test_rejeita_tipo_diferente_de_decimal(funcao: object, valor: object) -> None:
    with pytest.raises(TypeError, match="use Decimal, nunca float"):
        funcao(valor)  # type: ignore[operator]


@pytest.mark.parametrize("funcao", [quantize_brl, quantize_qty, quantize_pct])
@pytest.mark.parametrize("valor", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_rejeita_decimal_nao_finito(funcao: object, valor: str) -> None:
    with pytest.raises(ValueError, match="valor monetario/quantidade precisa ser finito"):
        funcao(Decimal(valor))  # type: ignore[operator]


# ---- Propriedade: erro de arredondamento no maximo meia unidade --------------


@given(
    st.decimals(
        min_value=Decimal("-1000000000"),
        max_value=Decimal("1000000000"),
        allow_nan=False,
        allow_infinity=False,
        places=8,
    )
)
def test_quantizacao_erra_no_maximo_meia_unidade(valor: Decimal) -> None:
    for funcao, unidade in ((quantize_brl, BRL), (quantize_qty, QTY), (quantize_pct, PCT)):
        resultado = funcao(valor)
        assert abs(resultado - valor) <= unidade / 2
        assert resultado.as_tuple().exponent == unidade.as_tuple().exponent
