"""Parcelas por maior resto (``installments.py``).

Mensagens e params testados por igualdade exata. As propriedades Hypothesis
cobrem soma exata, proximidade da cota exata, monotonicidade nos pesos,
determinismo e o minimo por parcela; os exemplos fixam o desempate
``(-resto, indice)``. Aritmetica de referencia dos testes em ``Fraction``
(exata), independente da implementacao.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.contract import Contract
from app.domain.errors import DomainValidationError, ErrorCode
from app.domain.installments import (
    MAX_PARCELAS,
    PESO_MAX,
    ParcelaCalculada,
    calcular_parcelas,
)
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada

D0 = date(2026, 9, 4)


def _datas(n: int) -> list[date]:
    return [D0 + timedelta(days=30 * i) for i in range(n)]


def _calc(total: str, pesos: list[int]) -> tuple[list[str], list[str]]:
    """(porcentagens, valores) como str, para comparar escala e valor de uma vez."""
    parcelas = calcular_parcelas(Decimal(total), pesos, _datas(len(pesos)))
    return [str(p.porcentagem) for p in parcelas], [str(p.valor) for p in parcelas]


def _erros(
    total: object, pesos: object, datas: object
) -> list[tuple[str, ErrorCode, dict[str, Any]]]:
    with pytest.raises(DomainValidationError) as exc:
        calcular_parcelas(total, pesos, datas)  # type: ignore[arg-type]
    return [(e.path, e.code, dict(e.params)) for e in exc.value.errors]


# ---- Exemplos: maior resto real ----------------------------------------------


def test_parcelas_iguais_residuo_vai_para_os_menores_indices() -> None:
    assert _calc("100.00", [1, 1, 1]) == (
        ["33.3334", "33.3333", "33.3333"],
        ["33.34", "33.33", "33.33"],
    )


def test_bate_com_o_payload_exemplo() -> None:
    assert _calc("23299.55", [1, 1, 1]) == (
        ["33.3334", "33.3333", "33.3333"],
        ["7766.52", "7766.52", "7766.51"],
    )


@pytest.mark.parametrize(
    ("pesos", "pcts", "valores"),
    [
        ([30, 70], ["30.0000", "70.0000"], ["30.00", "70.00"]),
        (
            [1, 1, 1, 97],
            ["1.0000", "1.0000", "1.0000", "97.0000"],
            ["1.00", "1.00", "1.00", "97.00"],
        ),
    ],
)
def test_pesos_desiguais_exatos_sem_residuo(
    pesos: list[int], pcts: list[str], valores: list[str]
) -> None:
    assert _calc("100.00", pesos) == (pcts, valores)


def test_pesos_desiguais_com_residuo() -> None:
    assert _calc("100.00", [1, 1, 1, 3]) == (
        ["16.6667", "16.6667", "16.6666", "50.0000"],
        ["16.67", "16.67", "16.66", "50.00"],
    )


def test_maior_resto_vence_o_menor_indice() -> None:
    """Chave (-resto, indice): resto 2 (indice 1) antes de resto 1 (indice 0)."""
    assert _calc("0.10", [1, 2]) == (["33.3333", "66.6667"], ["0.03", "0.07"])


def test_empate_de_resto_com_pesos_diferentes_vai_para_o_menor_indice() -> None:
    """[1, 2, 1] em 10 centavos: exatos 2.5 / 5 / 2.5; restos 2, 0, 2 -> indice 0."""
    assert _calc("0.10", [1, 2, 1])[1] == ["0.03", "0.05", "0.02"]


def test_residuo_de_porcentagem_e_de_valor_sao_independentes() -> None:
    """[1, 2, 1, 2]: na % os restos maiores sao dos pesos 1 (indices 0 e 2); no
    valor (20 centavos), dos pesos 2 (indices 1 e 3)."""
    pcts, valores = _calc("0.20", [1, 2, 1, 2])
    assert pcts == ["16.6667", "33.3333", "16.6667", "33.3333"]
    assert valores == ["0.03", "0.07", "0.03", "0.07"]


def test_uma_parcela() -> None:
    assert _calc("1234.56", [7]) == (["100.0000"], ["1234.56"])


def test_numera_de_1_a_n_e_preserva_as_datas() -> None:
    datas = _datas(4)
    parcelas = calcular_parcelas(Decimal("10.00"), [1, 2, 3, 4], datas)
    assert all(type(p) is ParcelaCalculada for p in parcelas)
    assert [p.parcela for p in parcelas] == [1, 2, 3, 4]
    assert [p.data for p in parcelas] == datas


def test_total_sem_casas_sai_com_duas() -> None:
    assert _calc("100", [1])[1] == ["100.00"]
    assert _calc("1E+2", [1])[1] == ["100.00"]


def test_aceita_tupla_e_nao_altera_a_entrada() -> None:
    pesos, datas = [1, 2], _datas(2)
    antes = (copy.copy(pesos), copy.copy(datas))
    calcular_parcelas(Decimal("1.00"), pesos, datas)
    assert (pesos, datas) == antes
    assert calcular_parcelas(Decimal("1.00"), tuple(pesos), tuple(datas)) == calcular_parcelas(
        Decimal("1.00"), pesos, datas
    )


def test_parcela_calculada_e_imutavel() -> None:
    p = calcular_parcelas(Decimal("1.00"), [1], _datas(1))[0]
    with pytest.raises(AttributeError):
        p.valor = Decimal("2.00")  # type: ignore[misc]


# ---- Validacao: total --------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "erro"),
    [
        (None, (ErrorCode.REQUIRED, {})),
        (100.0, (ErrorCode.NOT_DECIMAL, {})),
        (100, (ErrorCode.NOT_DECIMAL, {})),
        ("100.00", (ErrorCode.NOT_DECIMAL, {})),
        (Decimal("NaN"), (ErrorCode.NOT_FINITE, {})),
        (Decimal("Infinity"), (ErrorCode.NOT_FINITE, {})),
        (Decimal("10.001"), (ErrorCode.DECIMAL_SCALE, {"max": 2})),
        (Decimal("10000000000000.00"), (ErrorCode.DECIMAL_PRECISION, {"max": 13})),
        (Decimal("0"), (ErrorCode.MUST_BE_POSITIVE, {})),
        (Decimal("0.00"), (ErrorCode.MUST_BE_POSITIVE, {})),
        (Decimal("-1.00"), (ErrorCode.MUST_BE_POSITIVE, {})),
    ],
)
def test_total_invalido(total: object, erro: tuple[ErrorCode, dict[str, Any]]) -> None:
    assert _erros(total, [1], _datas(1)) == [("total", *erro)]


def test_total_no_limite_da_precisao_e_aceito() -> None:
    assert _calc("9999999999999.99", [1])[1] == ["9999999999999.99"]


# ---- Validacao: pesos --------------------------------------------------------


@pytest.mark.parametrize("pesos", [None, "111", 3, {1: 1}])
def test_pesos_precisa_ser_lista(pesos: object) -> None:
    assert _erros(Decimal("1.00"), pesos, _datas(1)) == [
        ("pesos", ErrorCode.INVALID_TYPE, {"tipo": "lista"})
    ]


def test_pesos_vazio() -> None:
    assert _erros(Decimal("1.00"), [], []) == [("pesos", ErrorCode.MIN_ITEMS, {"min": 1})]


def test_limite_de_parcelas() -> None:
    assert MAX_PARCELAS == 36
    assert len(calcular_parcelas(Decimal("1.00"), [1] * 36, _datas(36))) == 36
    assert _erros(Decimal("1.00"), [1] * 37, _datas(37)) == [
        ("pesos", ErrorCode.MAX_ITEMS, {"max": 36})
    ]


@pytest.mark.parametrize("peso", [0, -1, PESO_MAX + 1, True, False, 1.0, Decimal(1), "1", None])
def test_peso_invalido(peso: object) -> None:
    assert _erros(Decimal("100000.00"), [1, peso, 1], _datas(3)) == [
        ("pesos[1]", ErrorCode.INVALID_WEIGHT, {"max": 10000})
    ]


def test_peso_nos_limites() -> None:
    assert PESO_MAX == 10000
    assert _calc("100.01", [1, 10000]) == (["0.0100", "99.9900"], ["0.01", "100.00"])


def test_todos_os_pesos_invalidos_sao_apontados() -> None:
    assert [e[0] for e in _erros(Decimal("1.00"), [0, 1, -5, 10001], _datas(4))] == [
        "pesos[0]",
        "pesos[2]",
        "pesos[3]",
    ]


# ---- Validacao: datas (TODO(decisao #12)) ------------------------------------


@pytest.mark.parametrize("datas", [None, "2026-09-04", D0])
def test_datas_precisa_ser_lista(datas: object) -> None:
    assert _erros(Decimal("1.00"), [1], datas) == [
        ("datas", ErrorCode.INVALID_TYPE, {"tipo": "lista"})
    ]


@pytest.mark.parametrize("n_datas", [0, 2, 4])
def test_datas_e_pesos_do_mesmo_tamanho(n_datas: int) -> None:
    assert _erros(Decimal("1.00"), [1, 1, 1], _datas(n_datas)) == [
        ("datas", ErrorCode.LENGTH_MISMATCH, {"esperado": 3, "recebido": n_datas})
    ]


@pytest.mark.parametrize(
    "data",
    [datetime(2026, 10, 4, tzinfo=None), "2026-10-04", None, 20261004],  # noqa: DTZ001
)
def test_data_invalida(data: object) -> None:
    datas: list[object] = [D0, data, D0 + timedelta(days=60)]
    assert _erros(Decimal("1.00"), [1, 1, 1], datas) == [
        ("datas[1]", ErrorCode.INVALID_TYPE, {"tipo": "data"})
    ]


def test_datas_iguais_sao_rejeitadas() -> None:
    assert _erros(Decimal("1.00"), [1, 1], [D0, D0]) == [
        ("datas[1]", ErrorCode.DATES_NOT_INCREASING, {})
    ]


def test_datas_decrescentes_sao_rejeitadas_em_cada_quebra() -> None:
    d = [D0, D0 - timedelta(days=1), D0 + timedelta(days=5), D0 + timedelta(days=4)]
    assert _erros(Decimal("1.00"), [1, 1, 1, 1], d) == [
        ("datas[1]", ErrorCode.DATES_NOT_INCREASING, {}),
        ("datas[3]", ErrorCode.DATES_NOT_INCREASING, {}),
    ]


def test_segue_validando_depois_de_uma_data_invalida() -> None:
    d: list[object] = [D0, None, D0 + timedelta(days=1), D0 + timedelta(days=1)]
    assert _erros(Decimal("1.00"), [1, 1, 1, 1], d) == [
        ("datas[1]", ErrorCode.INVALID_TYPE, {"tipo": "data"}),
        ("datas[3]", ErrorCode.DATES_NOT_INCREASING, {}),
    ]


def test_um_dia_depois_basta() -> None:
    assert len(calcular_parcelas(Decimal("1.00"), [1, 1], [D0, D0 + timedelta(days=1)])) == 2


def test_data_invalida_nao_e_comparada() -> None:
    """So pares consecutivos validos sao comparados; o invalido ja deu erro de tipo."""
    d: list[object] = [D0, None, D0 - timedelta(days=9)]
    assert _erros(Decimal("1.00"), [1, 1, 1], d) == [
        ("datas[1]", ErrorCode.INVALID_TYPE, {"tipo": "data"})
    ]


# ---- Minimo por parcela (cota exata) -----------------------------------------


@pytest.mark.parametrize(
    ("pesos", "minimo"),
    [
        ([1, 1, 1], "0.03"),  # soma 3 / menor 1 = 3 centavos
        ([1, 4], "0.05"),  # divisivel: 5 / 1
        ([2, 3], "0.03"),  # 5 / 2 = 2.5 -> teto 3 centavos
        ([3, 7, 11], "0.07"),  # 21 / 3 = 7
        ([1] * 36, "0.36"),
        ([1] + [10000] * 35, "3500.01"),
    ],
)
def test_minimo_total_e_exato(pesos: list[int], minimo: str) -> None:
    n = len(pesos)
    abaixo = Decimal(minimo) - Decimal("0.01")
    if abaixo > 0:
        assert _erros(abaixo, pesos, _datas(n)) == [
            (
                "total",
                ErrorCode.INSTALLMENT_BELOW_MINIMUM,
                {"minimo_total": minimo, "parcelas": n},
            )
        ]
    parcelas = calcular_parcelas(Decimal(minimo), pesos, _datas(n))
    assert min(p.valor for p in parcelas) >= Decimal("0.01")


def test_minimo_pela_cota_exata_rejeita_o_que_o_maior_resto_salvaria() -> None:
    """[1, 4] em 0.04 daria 0.01 + 0.03, mas a cota exata da 1a e 0.008 < 0.01."""
    erros = _erros(Decimal("0.04"), [1, 4], _datas(2))
    assert erros == [
        ("total", ErrorCode.INSTALLMENT_BELOW_MINIMUM, {"minimo_total": "0.05", "parcelas": 2})
    ]


def test_mensagem_de_minimo_cita_as_duas_saidas() -> None:
    with pytest.raises(DomainValidationError) as exc:
        calcular_parcelas(Decimal("0.02"), [1, 1, 1], _datas(3))
    assert exc.value.errors[0].message == (
        "total insuficiente para 3 parcela(s): cada parcela precisa de ao menos 0.01; "
        "aumente o total para ao menos 0.03 ou equilibre os pesos"
    )


def test_minimo_so_e_checado_com_entrada_valida() -> None:
    """Com erro de campo, o minimo nao e calculado (nao daria para confiar nos pesos)."""
    assert _erros(Decimal("0.01"), [1, 1, 0], _datas(3)) == [
        ("pesos[2]", ErrorCode.INVALID_WEIGHT, {"max": 10000})
    ]


def test_erros_de_campo_acumulam_numa_excecao_so() -> None:
    assert _erros(Decimal("-1"), [0], [D0, D0]) == [
        ("total", ErrorCode.MUST_BE_POSITIVE, {}),
        ("pesos[0]", ErrorCode.INVALID_WEIGHT, {"max": 10000}),
        ("datas", ErrorCode.LENGTH_MISMATCH, {"esperado": 1, "recebido": 2}),
        ("datas[1]", ErrorCode.DATES_NOT_INCREASING, {}),
    ]


# ---- Propriedades (Hypothesis) -----------------------------------------------


@st.composite
def _entrada(draw: st.DrawFn) -> tuple[Decimal, list[int], list[date]]:
    pesos = draw(st.lists(st.integers(1, PESO_MAX), min_size=1, max_size=MAX_PARCELAS))
    minimo = -(-sum(pesos) // min(pesos))  # centavos
    centavos = draw(st.integers(minimo, 10**15 - 1))
    passos = draw(st.lists(st.integers(1, 400), min_size=len(pesos) - 1, max_size=len(pesos) - 1))
    datas = [D0]
    for p in passos:
        datas.append(datas[-1] + timedelta(days=p))
    return Decimal(centavos).scaleb(-2), pesos, datas


def _cotas(alvo: int, pesos: list[int]) -> list[Fraction]:
    return [Fraction(alvo * w, sum(pesos)) for w in pesos]


@given(_entrada())
def test_prop_soma_exata(entrada: tuple[Decimal, list[int], list[date]]) -> None:
    total, pesos, datas = entrada
    parcelas = calcular_parcelas(total, pesos, datas)
    assert sum(p.porcentagem for p in parcelas) == Decimal("100.0000")
    assert sum(p.valor for p in parcelas) == total


@given(_entrada())
def test_prop_proximidade_da_cota_exata(entrada: tuple[Decimal, list[int], list[date]]) -> None:
    """Cada parcela e o piso da cota exata ou o piso + 1 unidade."""
    total, pesos, datas = entrada
    parcelas = calcular_parcelas(total, pesos, datas)
    centavos = int(total.scaleb(2))
    for p, cota_v, cota_p in zip(
        parcelas, _cotas(centavos, pesos), _cotas(1_000_000, pesos), strict=True
    ):
        v = int(p.valor.scaleb(2))
        pc = int(p.porcentagem.scaleb(4))
        assert abs(v - cota_v) < 1
        assert abs(pc - cota_p) < 1
        assert v in (int(cota_v), int(cota_v) + 1)
        assert pc in (int(cota_p), int(cota_p) + 1)


@given(_entrada())
def test_prop_monotonicidade(entrada: tuple[Decimal, list[int], list[date]]) -> None:
    """Peso maior nunca recebe menos; peso igual: indice menor recebe >= e ate 1 unidade a mais."""
    total, pesos, datas = entrada
    parcelas = calcular_parcelas(total, pesos, datas)
    for i, (wi, pi) in enumerate(zip(pesos, parcelas, strict=True)):
        for j in range(i + 1, len(pesos)):
            wj, pj = pesos[j], parcelas[j]
            for a, b, unidade in (
                (pi.valor, pj.valor, Decimal("0.01")),
                (pi.porcentagem, pj.porcentagem, Decimal("0.0001")),
            ):
                if wi > wj:
                    assert a >= b
                elif wi < wj:
                    assert a <= b
                else:
                    assert b <= a <= b + unidade


@given(_entrada())
def test_prop_determinismo(entrada: tuple[Decimal, list[int], list[date]]) -> None:
    total, pesos, datas = entrada
    copia = (copy.copy(pesos), copy.copy(datas))
    primeira = calcular_parcelas(total, pesos, datas)
    assert calcular_parcelas(total, pesos, datas) == primeira
    assert calcular_parcelas(total, tuple(pesos), tuple(datas)) == primeira
    assert (pesos, datas) == copia


@given(_entrada())
def test_prop_forma_e_minimo(entrada: tuple[Decimal, list[int], list[date]]) -> None:
    total, pesos, datas = entrada
    parcelas = calcular_parcelas(total, pesos, datas)
    assert [p.parcela for p in parcelas] == list(range(1, len(pesos) + 1))
    assert [p.data for p in parcelas] == datas
    for p in parcelas:
        assert p.valor.as_tuple().exponent == -2
        assert p.porcentagem.as_tuple().exponent == -4
        assert p.valor >= Decimal("0.01")
        assert p.porcentagem >= Decimal("0.0001")


@given(st.lists(st.integers(1, PESO_MAX), min_size=1, max_size=MAX_PARCELAS), st.data())
def test_prop_abaixo_do_minimo_sempre_falha_com_o_minimo_exato(
    pesos: list[int], data: st.DataObject
) -> None:
    minimo = -(-sum(pesos) // min(pesos))
    centavos = data.draw(st.integers(1, minimo - 1)) if minimo > 1 else None
    datas = _datas(len(pesos))
    if centavos is not None:
        assert _erros(Decimal(centavos).scaleb(-2), pesos, datas) == [
            (
                "total",
                ErrorCode.INSTALLMENT_BELOW_MINIMUM,
                {"minimo_total": str(Decimal(minimo).scaleb(-2)), "parcelas": len(pesos)},
            )
        ]
    calcular_parcelas(Decimal(minimo).scaleb(-2), pesos, datas)


# ---- Invariante: to_FormPag nunca vem do cliente -----------------------------


def _raiz() -> Path:
    for pasta in Path(__file__).resolve().parents:
        if (pasta / "docs" / "ARCHITECTURE.md").is_file() and (pasta / "CLAUDE.md").is_file():
            return pasta
    raise FileNotFoundError("raiz do repo nao encontrada acima de " + __file__)


def test_regra_to_formpag_nunca_vem_do_cliente_esta_documentada() -> None:
    """Teste de documentacao. A regra vale a partir do caso de uso (Fase 3):

    a entrada da API e ``total`` + ``pesos`` + ``datas`` + ``FormPag``; o schema
    de entrada NAO tem ``to_FormPag``; as parcelas sao sempre montadas por
    ``calcular_parcelas``. Quando o caso de uso existir, o teste dele substitui
    este (schema sem ``to_FormPag`` + parcelas vindas de ``calcular_parcelas``).
    """
    raiz = _raiz()
    marcador = "`to_FormPag` NUNCA vem do cliente"
    assert marcador in (raiz / "CLAUDE.md").read_text(encoding="utf-8")
    assert marcador in (raiz / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")


def test_parcelas_calculadas_montam_o_to_formpag_do_exemplo() -> None:
    """O fluxo da Fase 3 em miniatura: calcular_parcelas -> to_FormPag -> Contract.criar."""
    dados = payload_exemplo_como_entrada()
    original = dados["to_FormPag"]
    parcelas = calcular_parcelas(Decimal("23299.55"), [1, 1, 1], [p["Data"] for p in original])
    dados["to_FormPag"] = [
        {
            "Parcela": p.parcela,
            "TransactionCurrency": "BRL",
            "Porcentagem": p.porcentagem,
            "Valor": p.valor,
            "Data": p.data,
            "FormPag": "K",
        }
        for p in parcelas
    ]
    assert dados["to_FormPag"] == original
    c = Contract.criar(dados)
    assert [(i.parcela, i.porcentagem, i.valor, i.data) for i in c.installments] == [
        (p.parcela, p.porcentagem, p.valor, p.data) for p in parcelas
    ]
