"""Mapper dominio -> payload OData (``to_payload``) e serializacao (``to_json``).

Criticos (gate de mutacao): ``StatusBlock == "06"`` em QUALQUER contrato e
decimal com a escala do campo fixada, nunca ``str(Decimal)`` nem ``float``.
Golden: o ``payload_exemplo.json`` montado pelo dominio, com o ``to_FormPag``
vindo de ``calcular_parcelas``, nos dois modos da flag.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.contract import (
    CABECALHO,
    ESPECIFICACOES,
    ITEM,
    PARCEIRO,
    PARCELA,
    PRECO,
    TEXTO,
    Campo,
    Contract,
    Tipo,
)
from app.domain.installments import calcular_parcelas
from app.infrastructure.sap.mapper import STATUS_BLOCK, to_json, to_payload
from tests.unit.domain._referencias_sap import (
    metadata,
    navegacoes,
    payload_exemplo_como_entrada,
)

_COMPUTED = {"SalesContract", "SalesContractItem", "ConditionUUID"}
_ESCALA = {c.odata: c.escala for specs in ESPECIFICACOES.values() for c in specs if c.escala}


def _docs_sap() -> Path:
    for pasta in Path(__file__).resolve().parents:
        if (pasta / "docs" / "sap" / "payload_exemplo.json").is_file():
            return pasta / "docs" / "sap"
    raise FileNotFoundError("docs/sap nao encontrado acima de " + __file__)


def _exemplo_arquivo() -> dict[str, Any]:
    texto = (_docs_sap() / "payload_exemplo.json").read_text(encoding="utf-8")
    dados: dict[str, Any] = json.loads(texto, parse_float=Decimal)
    return dados


def _contrato_do_exemplo() -> Contract:
    """O fluxo do caso de uso: entrada sem to_FormPag + parcelas de calcular_parcelas."""
    dados = payload_exemplo_como_entrada()
    datas = [p["Data"] for p in dados.pop("to_FormPag")]
    parcelas = calcular_parcelas(Decimal("23299.55"), [1, 1, 1], datas)
    dados["to_FormPag"] = [
        {
            "Parcela": p.parcela,
            "Porcentagem": p.porcentagem,
            "Valor": p.valor,
            "Data": p.data,
            "FormPag": "K",
            "TransactionCurrency": "BRL",
        }
        for p in parcelas
    ]
    return Contract.criar(dados)


def _semantico(no: Any, chave: str | None = None) -> Any:
    """Normaliza para comparar: decimal (numero, int do arquivo ou string) -> Decimal."""
    if isinstance(no, dict):
        return {k: _semantico(v, k) for k, v in no.items()}
    if isinstance(no, list):
        return [_semantico(v, chave) for v in no]
    if chave in _ESCALA:
        return Decimal(no)
    return no


def _decimais(no: Any, chave: str | None = None) -> list[tuple[str, Any]]:
    """(campo, valor) de todo decimal do payload."""
    if isinstance(no, dict):
        return [par for k, v in no.items() for par in _decimais(v, k)]
    if isinstance(no, list):
        return [par for v in no for par in _decimais(v, chave)]
    return [(chave, no)] if chave in _ESCALA else []


def _literais_numericos(saida: bytes) -> list[str]:
    """Todo numero do JSON, como escrito (strings nao entram: "0E0" e texto valido)."""
    literais: list[str] = []

    def guardar(texto: str) -> str:
        literais.append(texto)
        return texto

    json.loads(saida, parse_float=guardar, parse_int=guardar)
    return literais


def _sem_notacao_cientifica(saida: bytes) -> bool:
    return not [n for n in _literais_numericos(saida) if "e" in n.lower()]


def _chaves(no: Any) -> set[str]:
    if isinstance(no, dict):
        return set(no) | {k for v in no.values() for k in _chaves(v)}
    if isinstance(no, list):
        return {k for v in no for k in _chaves(v)}
    return set()


# ---- Golden ------------------------------------------------------------------


@pytest.mark.parametrize("como_string", [False, True])
def test_golden_payload_exemplo(como_string: bool) -> None:
    payload = to_payload(_contrato_do_exemplo(), decimal_as_string=como_string)
    assert _semantico(payload) == _semantico(_exemplo_arquivo())


@pytest.mark.parametrize("como_string", [False, True])
def test_golden_pelo_json(como_string: bool) -> None:
    saida = to_json(to_payload(_contrato_do_exemplo(), decimal_as_string=como_string))
    assert _semantico(json.loads(saida, parse_float=Decimal)) == _semantico(_exemplo_arquivo())


def test_golden_modo_numero_escala_fixa_no_json() -> None:
    saida = to_json(to_payload(_contrato_do_exemplo(), decimal_as_string=False))
    for literal in (
        b'"Valor":7766.52',
        b'"Porcentagem":33.3334',
        b'"RequestedQuantity":1.000',
        b'"ConditionRateValue":1164.980000000',
        b'"ConditionRateValue":1.500000000',
        b'"Parcela":1',
    ):
        assert literal in saida


def test_golden_modo_string_escala_fixa_no_json() -> None:
    saida = to_json(to_payload(_contrato_do_exemplo(), decimal_as_string=True))
    for literal in (
        b'"Valor":"7766.51"',
        b'"RequestedQuantity":"1.000"',
        b'"ConditionRateValue":"2.320000000"',
        b'"Parcela":1',  # Int32 nao vira string (IEEE754Compatible so afeta Decimal/Int64)
    ):
        assert literal in saida


# ---- StatusBlock, Computed, strings, datas -----------------------------------


@pytest.mark.parametrize("como_string", [False, True])
def test_status_block_06_logo_depois_do_cod_taxa(como_string: bool) -> None:
    payload = to_payload(_contrato_do_exemplo(), decimal_as_string=como_string)
    assert STATUS_BLOCK == "06"
    assert payload["StatusBlock"] == "06"
    chaves = list(payload)
    assert chaves[chaves.index("CodTaxa") + 1] == "StatusBlock"


def test_datas_none_sao_omitidas_e_as_presentes_em_iso() -> None:
    c = _contrato_do_exemplo()
    sem = dataclasses.replace(
        c,
        header=dataclasses.replace(c.header, customer_purchase_order_date=None),
        items=(dataclasses.replace(c.items[0], schedule_date2=None),),
        installments=tuple(dataclasses.replace(p, data=None) for p in c.installments),
    )
    payload = to_payload(sem, decimal_as_string=False)
    assert "CustomerPurchaseOrderDate" not in payload
    assert payload["SalesContractValidityEndDate"] == "2027-08-05"
    assert "ScheduleDate2" not in payload["to_Item"][0]
    assert payload["to_Item"][0]["ScheduleDate"] == "2026-08-30"
    assert all("Data" not in p for p in payload["to_FormPag"])


def test_string_vazia_sai_como_vazia_e_nunca_null() -> None:
    c = _contrato_do_exemplo()
    vazio = dataclasses.replace(
        c, header=dataclasses.replace(c.header, sales_office="", cod_taxa="", pedido_sysfertil="")
    )
    payload = to_payload(vazio, decimal_as_string=True)
    assert (payload["SalesOffice"], payload["CodTaxa"], payload["PedidoSysFertil"]) == ("", "", "")
    assert payload["to_Partner"][0]["Supplier"] == ""
    assert b"null" not in to_json(payload)


def test_listas_vazias_saem_como_lista() -> None:
    c = _contrato_do_exemplo()
    vazio = dataclasses.replace(
        c,
        partners=(),
        pricing=(),
        texts=(),
        installments=(),
        items=(dataclasses.replace(c.items[0], pricing=()),),
    )
    payload = to_payload(vazio, decimal_as_string=False)
    for nav in ("to_FormPag", "to_Partner", "to_PricingElement", "to_Text"):
        assert payload[nav] == []
    assert payload["to_Item"][0]["to_PricingElement"] == []


def test_nao_muta_o_contrato_e_e_deterministico() -> None:
    c = _contrato_do_exemplo()
    copia = dataclasses.replace(c)
    primeiro = to_json(to_payload(c, decimal_as_string=False))
    assert to_json(to_payload(c, decimal_as_string=False)) == primeiro
    assert c == copia


# ---- Decimal: escala fixa, nunca expoente, nunca float -----------------------


def _com_quantidade(q: Decimal) -> Contract:
    c = _contrato_do_exemplo()
    return dataclasses.replace(c, items=(dataclasses.replace(c.items[0], requested_quantity=q),))


def _com_taxa(taxa: Decimal) -> Contract:
    c = _contrato_do_exemplo()
    preco = dataclasses.replace(c.pricing[0], condition_rate_value=taxa)
    return dataclasses.replace(c, pricing=(preco, *c.pricing[1:]))


@pytest.mark.parametrize(
    ("entrada", "texto"),
    [
        (Decimal("1E+2"), "100.000"),
        (Decimal("1"), "1.000"),
        (Decimal("1.5"), "1.500"),
        (Decimal("12345678.9"), "12345678.900"),
        (Decimal("0.001"), "0.001"),
        (Decimal("1.10000"), "1.100"),  # zeros a direita alem da escala: mesmo valor
    ],
)
def test_quantidade_com_escala_3_nos_dois_modos(entrada: Decimal, texto: str) -> None:
    string = to_payload(_com_quantidade(entrada), decimal_as_string=True)
    numero = to_payload(_com_quantidade(entrada), decimal_as_string=False)
    assert string["to_Item"][0]["RequestedQuantity"] == texto
    valor = numero["to_Item"][0]["RequestedQuantity"]
    assert type(valor) is Decimal
    assert valor == entrada
    assert valor.as_tuple().exponent == -3
    assert f'"RequestedQuantity":{texto}'.encode() in to_json(numero)


@pytest.mark.parametrize(
    ("entrada", "texto"),
    [
        (Decimal("0.0000001"), "0.000000100"),
        (Decimal("1E-9"), "0.000000001"),
        (Decimal("2.5E+3"), "2500.000000000"),
        (Decimal("-1.5"), "-1.500000000"),
        (Decimal("-0"), "0.000000000"),
        (Decimal("-0E-12"), "0.000000000"),
        (Decimal("0"), "0.000000000"),
    ],
)
def test_taxa_com_escala_9_sem_expoente_e_sem_menos_zero(entrada: Decimal, texto: str) -> None:
    string = to_payload(_com_taxa(entrada), decimal_as_string=True)
    numero = to_payload(_com_taxa(entrada), decimal_as_string=False)
    assert string["to_PricingElement"][0]["ConditionRateValue"] == texto
    assert to_json(numero).count(f'"ConditionRateValue":{texto}}}'.encode()) == 1
    assert _sem_notacao_cientifica(to_json(numero))
    assert texto in _literais_numericos(to_json(numero))


def test_valor_com_mais_casas_que_a_escala_e_erro_nunca_arredonda() -> None:
    with pytest.raises(
        ValueError, match=r"^RequestedQuantity: 1\.2345 tem mais de 3 casas decimais$"
    ):
        to_payload(_com_quantidade(Decimal("1.2345")), decimal_as_string=True)


@pytest.mark.parametrize("valor", [None, Decimal("NaN"), Decimal("Infinity"), 1.5, 2])
def test_decimal_ausente_ou_invalido_e_barrado_no_mapper(valor: object) -> None:
    """Segunda barreira: o Contract.criar ja exige; aqui e defesa contra uso programatico."""
    c = _contrato_do_exemplo()
    parcela = dataclasses.replace(c.installments[1])
    object.__setattr__(parcela, "valor", valor)  # fura o __post_init__ de proposito
    ruim = dataclasses.replace(c, installments=(c.installments[0], parcela, c.installments[2]))
    with pytest.raises(ValueError, match=r"^Valor: decimal ausente ou invalido$"):
        to_payload(ruim, decimal_as_string=False)


def test_escala_do_mesmo_campo_e_igual_em_toda_entidade() -> None:
    """O mapper indexa a escala pelo nome OData: tem que ser unica por nome."""
    por_nome: dict[str, set[int]] = {}
    for specs in ESPECIFICACOES.values():
        for c in specs:
            if c.escala is not None:
                por_nome.setdefault(c.odata, set()).add(c.escala)
    assert por_nome == {
        "RequestedQuantity": {3},
        "ConditionRateValue": {9},
        "Porcentagem": {4},
        "Valor": {2},
    }


# ---- to_json -----------------------------------------------------------------


def test_to_json_bytes_compacto_e_exato() -> None:
    payload = {"a": "xç", "n": Decimal("1.50"), "i": 3, "l": [{"b": ""}], "v": []}
    saida = to_json(payload)
    assert type(saida) is bytes
    assert saida == b'{"a":"x\\u00e7","n":1.50,"i":3,"l":[{"b":""}],"v":[]}'


def test_to_json_escapa_strings_e_chaves() -> None:
    assert to_json({'a"b': 'c"\n'}) == b'{"a\\"b":"c\\"\\n"}'


@pytest.mark.parametrize("valor", [1.5, None, True, False, (1, 2), {1, 2}, date(2026, 1, 1)])
def test_to_json_recusa_o_que_nao_e_payload(valor: object) -> None:
    """float nunca; null nunca (string vazia e ""); bool/tupla/data nao existem no payload."""
    with pytest.raises(TypeError, match=r"^to_json: tipo nao suportado \(\w+\)$"):
        to_json({"x": valor})


@pytest.mark.parametrize("valor", [Decimal("NaN"), Decimal("-Infinity"), Decimal("sNaN")])
def test_to_json_recusa_decimal_nao_finito(valor: Decimal) -> None:
    with pytest.raises(ValueError, match=r"^to_json: decimal nao finito$"):
        to_json({"x": valor})


def test_to_json_chave_precisa_ser_str() -> None:
    with pytest.raises(TypeError, match=r"^to_json: chave precisa ser str$"):
        to_json({1: "x"})  # type: ignore[dict-item]


# ---- Hypothesis: contratos validos variados ----------------------------------

_ALFABETO = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _valor(c: Campo) -> st.SearchStrategy[Any]:
    if c.tipo is Tipo.TEXTO:
        tamanho = min(c.max_len or 20, 20)
        return st.text(_ALFABETO, min_size=1 if c.obrigatorio else 0, max_size=tamanho)
    if c.tipo is Tipo.DATA:
        return st.none() | st.dates(date(2000, 1, 1), date(2099, 12, 31))
    if c.tipo is Tipo.INTEIRO:
        return st.integers(1, 10**6)
    assert c.escala is not None
    assert c.precisao is not None
    escala = c.escala
    limite = 10**c.precisao - 1  # em unidades da escala
    unidades = st.integers(1 if c.positivo else -limite, limite)
    return unidades.map(lambda u: Decimal(u).scaleb(-escala))


def _entidade(specs: tuple[Campo, ...]) -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries({c.odata: _valor(c) for c in specs})


@st.composite
def _contratos(draw: st.DrawFn) -> Contract:
    dados = draw(_entidade(CABECALHO))
    itens = draw(st.lists(_entidade(ITEM), min_size=1, max_size=3))
    for item in itens:
        item["to_PricingElement"] = draw(st.lists(_entidade(PRECO), max_size=3))
    dados["to_Item"] = itens
    funcoes = draw(st.lists(st.text(_ALFABETO, min_size=1, max_size=2), unique=True, max_size=3))
    dados["to_Partner"] = [
        {**draw(_entidade(PARCEIRO)), "PartnerFunction": f, "Customer": "1"} for f in funcoes
    ]
    dados["to_PricingElement"] = draw(st.lists(_entidade(PRECO), max_size=3))
    n = draw(st.integers(0, 4))
    if n:
        pesos = draw(st.lists(st.integers(1, 10), min_size=n, max_size=n))
        centavos = draw(st.integers(-(-sum(pesos) // min(pesos)), 10**9))
        datas = [date(2026, 1, 1) + timedelta(days=30 * i) for i in range(n)]
        parcelas = calcular_parcelas(Decimal(centavos).scaleb(-2), pesos, datas)
        base = draw(_entidade(PARCELA))
        dados["to_FormPag"] = [
            {
                **base,
                "Parcela": p.parcela,
                "Porcentagem": p.porcentagem,
                "Valor": p.valor,
                "Data": p.data,
            }
            for p in parcelas
        ]
    dados["to_Text"] = draw(st.lists(_entidade(TEXTO), max_size=2))
    return Contract.criar(dados)


def _ordem_esperada(entidade: str, obj: dict[str, Any]) -> list[str]:
    props = [n for n in metadata()[entidade] if n in obj]
    navs = [n for n in navegacoes()[entidade] if n in obj]
    return props + navs


_NAV_ENTIDADE = {
    "to_FormPag": "ParcelasContratoType",
    "to_Item": "ItensContratoType",
    "to_Partner": "ParceirosContratoType",
    "to_PricingElement": "PrecosCabecalhoType",
    "to_Text": "TextosContratoType",
}


@given(_contratos(), st.booleans())
def test_prop_status_block_computed_e_ordem(c: Contract, como_string: bool) -> None:
    payload = to_payload(c, decimal_as_string=como_string)
    assert payload["StatusBlock"] == "06"
    assert not _chaves(payload) & _COMPUTED
    assert not _chaves(payload) & {"_Contract", "_Item"}
    assert list(payload) == _ordem_esperada("CriaContratoType", payload)
    assert list(payload)[-5:] == list(navegacoes()["CriaContratoType"])
    for nav, entidade in _NAV_ENTIDADE.items():
        for el in payload[nav]:
            assert list(el) == _ordem_esperada(entidade, el)
    for item in payload["to_Item"]:
        for preco in item["to_PricingElement"]:
            assert list(preco) == _ordem_esperada("PrecosItemType", preco)


@given(_contratos())
def test_prop_decimais_escala_fixa_nos_dois_modos(c: Contract) -> None:
    string = to_payload(c, decimal_as_string=True)
    numero = to_payload(c, decimal_as_string=False)
    pares = zip(_decimais(string), _decimais(numero), strict=True)
    for (campo, texto), (_, valor) in pares:
        escala = _ESCALA[campo]
        assert type(texto) is str
        assert type(valor) is Decimal
        assert re.fullmatch(rf"-?\d+\.\d{{{escala}}}", texto)
        assert Decimal(texto) == valor
        assert valor.as_tuple().exponent == -escala
        assert texto == format(valor, "f")


@given(_contratos(), st.booleans())
def test_prop_to_json_round_trip_exato_sem_notacao_cientifica(
    c: Contract, como_string: bool
) -> None:
    payload = to_payload(c, decimal_as_string=como_string)
    saida = to_json(payload)
    assert _sem_notacao_cientifica(saida)
    assert b"null" not in saida
    assert json.loads(saida, parse_float=Decimal) == payload
