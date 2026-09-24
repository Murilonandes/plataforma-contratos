"""VOs do contrato: ``Contract.criar(dados)`` valida TUDO e levanta uma vez.

Entrada no formato do payload OData (PascalCase, ``to_*``), ja tipada
(``Decimal``, ``date``, ``int``). Normalizacao explicita: strip em strings e
uppercase so em campo ``IsUpperCase`` (FormPag). Nada mais e transformado:
escala acima da permitida e erro, nunca arredondamento.
"""

from __future__ import annotations

import copy
import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.domain.contract import Contract, Header, Installment, Item, Partner, PricingElement, Text
from app.domain.errors import DomainValidationError, ErrorCode
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada


def _valido() -> dict[str, Any]:
    return payload_exemplo_como_entrada()


def _erros(dados: dict[str, Any]) -> list[tuple[str, ErrorCode, dict[str, object]]]:
    with pytest.raises(DomainValidationError) as exc:
        Contract.criar(dados)
    return [(e.path, e.code, dict(e.params)) for e in exc.value.errors]


def _um_erro(dados: dict[str, Any]) -> tuple[str, ErrorCode, dict[str, object]]:
    erros = _erros(dados)
    assert len(erros) == 1, erros
    return erros[0]


# ---- Sanidade: o payload de referencia passa ---------------------------------


def test_payload_exemplo_passa_sem_erro() -> None:
    c = Contract.criar(_valido())
    assert c.header.sales_contract_type == "ZCON"
    assert c.header.sold_to_party == "1003874"
    assert c.header.customer_purchase_order_date == date(2026, 8, 5)
    assert len(c.items) == 1
    item = c.items[0]
    assert item.material == "20209"
    assert item.requested_quantity == Decimal("1")
    assert item.requested_quantity.as_tuple().exponent == -3  # canonizado via money
    assert [p.condition_type for p in item.pricing] == ["PR00", "ZFRE"]
    assert item.pricing[0].condition_rate_value == Decimal("1164.98")
    assert [p.partner_function for p in c.partners] == ["Y1", "Y2"]
    assert [p.condition_type for p in c.pricing] == ["ZCM1", "ZCM2"]
    assert [i.parcela for i in c.installments] == [1, 2, 3]
    assert sum(i.porcentagem or 0 for i in c.installments) == Decimal("100.0000")
    assert c.installments[0].valor == Decimal("7766.52")
    assert c.texts[0].long_text == "teste_LongText"


def test_payload_exemplo_nao_e_alterado_pela_validacao() -> None:
    dados = _valido()
    original = copy.deepcopy(dados)
    Contract.criar(dados)
    assert dados == original


# ---- Ponta a ponta: acumula e levanta UMA vez --------------------------------


def test_tres_problemas_em_lugares_diferentes_voltam_os_tres() -> None:
    dados = _valido()
    dados["SalesContractType"] = "   "  # header: obrigatorio vazio apos strip
    dados["to_Item"].append(copy.deepcopy(dados["to_Item"][0]))
    dados["to_Item"][1]["Material"] = ""  # item[1]
    dados["to_FormPag"][2]["Porcentagem"] = Decimal("33.33331")  # parcela[2]: 5 casas
    assert _erros(dados) == [
        ("SalesContractType", ErrorCode.REQUIRED, {}),
        ("to_Item[1].Material", ErrorCode.REQUIRED, {}),
        ("to_FormPag[2].Porcentagem", ErrorCode.DECIMAL_SCALE, {"max": 4}),
    ]


def test_acumula_muitos_erros_do_contrato_inteiro() -> None:
    dados = _valido()
    dados["SalesOrganization"] = "BRF12"  # > 4
    dados["DistributionChannel"] = None  # obrigatorio
    dados["to_Item"][0]["RequestedQuantity"] = Decimal("0")
    dados["to_Item"][0]["to_PricingElement"][1]["ConditionType"] = ""
    dados["to_Partner"][1]["PartnerFunction"] = "YYY"
    dados["to_Text"][0]["Language"] = ""
    caminhos = [p for p, _, _ in _erros(dados)]
    assert caminhos == [
        "SalesOrganization",
        "DistributionChannel",
        "to_Item[0].RequestedQuantity",
        "to_Item[0].to_PricingElement[1].ConditionType",
        "to_Partner[1].PartnerFunction",
        "to_Text[0].Language",
    ]


# ---- Normalizacao: strip; uppercase so em IsUpperCase ------------------------


def test_strip_em_todas_as_strings() -> None:
    dados = _valido()
    dados["SoldToParty"] = "  1003874 \t"
    dados["to_Item"][0]["Material"] = "\n20209 "
    dados["to_Partner"][0]["Customer"] = " 1002138"
    c = Contract.criar(dados)
    assert c.header.sold_to_party == "1003874"
    assert c.items[0].material == "20209"
    assert c.partners[0].customer == "1002138"


def test_uppercase_so_no_form_pag() -> None:
    dados = _valido()
    dados["to_FormPag"][0]["FormPag"] = " k "
    dados["to_Partner"][0]["PartnerFunction"] = "y1"
    dados["to_Item"][0]["to_PricingElement"][0]["ConditionType"] = "pr00"
    dados["SalesOffice"] = "ecwb"
    c = Contract.criar(dados)
    assert c.installments[0].form_pag == "K"
    assert c.partners[0].partner_function == "y1"  # sem IsUpperCase: nao transforma
    assert c.items[0].pricing[0].condition_type == "pr00"
    assert c.header.sales_office == "ecwb"


def test_max_length_e_validado_depois_do_strip() -> None:
    dados = _valido()
    dados["to_Item"][0]["SalesContractItemText"] = "   " + "x" * 40 + "   "
    assert Contract.criar(dados).items[0].sales_contract_item_text == "x" * 40
    dados["to_Item"][0]["SalesContractItemText"] = " " + "x" * 41 + " "
    assert _um_erro(dados) == (
        "to_Item[0].SalesContractItemText",
        ErrorCode.MAX_LENGTH,
        {"max": 40},
    )


def test_max_length_provisorio_do_long_text() -> None:
    dados = _valido()
    dados["to_Text"][0]["LongText"] = "x" * 1000
    Contract.criar(dados)
    dados["to_Text"][0]["LongText"] = "x" * 1001
    assert _um_erro(dados) == ("to_Text[0].LongText", ErrorCode.MAX_LENGTH, {"max": 1000})


@pytest.mark.parametrize(
    ("onde", "chave"),
    [
        ((), "NotaInternaCli"),
        ((), "PedidoSysFertil"),
        (("to_Item", 0), "Culture"),
    ],
)
def test_teto_provisorio_de_255_sem_max_length_no_metadata(
    onde: tuple[str, int] | tuple[()], chave: str
) -> None:
    """TODO(decisao #5): MaxLength real a confirmar com a Sysfertil."""
    dados = _valido()
    alvo = dados[onde[0]][onde[1]] if onde else dados
    path = f"{onde[0]}[{onde[1]}].{chave}" if onde else chave
    alvo[chave] = "x" * 255
    Contract.criar(dados)
    alvo[chave] = "x" * 256
    assert _um_erro(dados) == (path, ErrorCode.MAX_LENGTH, {"max": 255})


# ---- Obrigatoriedade (FieldControl/Mandatory) --------------------------------


@pytest.mark.parametrize("valor", [None, "", "   ", "\t\n"])
def test_obrigatorio_vazio_ausente_ou_so_espaco(valor: object) -> None:
    dados = _valido()
    dados["SoldToParty"] = valor
    assert _um_erro(dados) == ("SoldToParty", ErrorCode.REQUIRED, {})


def test_obrigatorio_chave_ausente() -> None:
    dados = _valido()
    del dados["to_Item"][0]["RequestedQuantityUnit"]
    assert _um_erro(dados) == ("to_Item[0].RequestedQuantityUnit", ErrorCode.REQUIRED, {})


@pytest.mark.parametrize(
    ("onde", "path"),
    [
        (("to_PricingElement", 1), "to_PricingElement[1].ConditionRateValue"),
        (
            ("to_Item", 0, "to_PricingElement", 0),
            "to_Item[0].to_PricingElement[0].ConditionRateValue",
        ),
    ],
)
@pytest.mark.parametrize("ausente", ["chave", None])
def test_condition_rate_value_nullable_false_e_exigido(
    onde: tuple[Any, ...], path: str, ausente: str | None
) -> None:
    """Nullable=false no metadata: sem valor nao ha payload valido (nem null, nem chave ausente)."""
    dados = _valido()
    alvo: Any = dados
    for passo in onde:
        alvo = alvo[passo]
    if ausente == "chave":
        del alvo["ConditionRateValue"]
    else:
        alvo["ConditionRateValue"] = None
    assert _um_erro(dados) == (path, ErrorCode.REQUIRED, {})


def test_opcional_ausente_vira_default() -> None:
    dados = _valido()
    for chave in ("SalesOffice", "CodTaxa", "CustomerPurchaseOrderDate", "to_Text", "to_Partner"):
        del dados[chave]
    del dados["to_FormPag"][0]["Porcentagem"]
    c = Contract.criar(dados)
    assert c.header.sales_office == ""
    assert c.header.cod_taxa == ""
    assert c.header.customer_purchase_order_date is None
    assert c.texts == ()
    assert c.partners == ()
    assert c.installments[0].porcentagem is None


def test_decimal_obrigatorio_ausente() -> None:
    dados = _valido()
    dados["to_Item"][0]["RequestedQuantity"] = None
    assert _um_erro(dados) == ("to_Item[0].RequestedQuantity", ErrorCode.REQUIRED, {})


def test_inteiro_obrigatorio_ausente() -> None:
    dados = _valido()
    del dados["to_FormPag"][1]["Parcela"]
    assert _um_erro(dados) == ("to_FormPag[1].Parcela", ErrorCode.REQUIRED, {})


# ---- Tipos -------------------------------------------------------------------


@pytest.mark.parametrize("valor", [1.5, 1, True, "1.5"])
def test_decimal_so_aceita_decimal(valor: object) -> None:
    dados = _valido()
    dados["to_Item"][0]["RequestedQuantity"] = valor
    assert _um_erro(dados) == ("to_Item[0].RequestedQuantity", ErrorCode.NOT_DECIMAL, {})


@pytest.mark.parametrize("valor", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_decimal_nao_finito(valor: str) -> None:
    dados = _valido()
    dados["to_PricingElement"][0]["ConditionRateValue"] = Decimal(valor)
    assert _um_erro(dados) == ("to_PricingElement[0].ConditionRateValue", ErrorCode.NOT_FINITE, {})


@pytest.mark.parametrize("valor", ["2026-08-05", datetime(2026, 8, 5, tzinfo=UTC), 20260805])
def test_data_so_aceita_date(valor: object) -> None:
    dados = _valido()
    dados["SalesContractValidityEndDate"] = valor
    assert _um_erro(dados) == (
        "SalesContractValidityEndDate",
        ErrorCode.INVALID_TYPE,
        {"tipo": "data"},
    )


@pytest.mark.parametrize("valor", [Decimal("1"), 1.0, True, "1"])
def test_inteiro_so_aceita_int(valor: object) -> None:
    dados = _valido()
    dados["to_FormPag"][0]["Parcela"] = valor
    assert _um_erro(dados) == ("to_FormPag[0].Parcela", ErrorCode.INVALID_TYPE, {"tipo": "inteiro"})


@pytest.mark.parametrize("valor", [20209, Decimal("20209"), ["20209"]])
def test_texto_so_aceita_str(valor: object) -> None:
    dados = _valido()
    dados["to_Item"][0]["Material"] = valor
    assert _um_erro(dados) == ("to_Item[0].Material", ErrorCode.INVALID_TYPE, {"tipo": "texto"})


@pytest.mark.parametrize("valor", [{"x": 1}, "to_Item", 3])
def test_lista_precisa_ser_lista(valor: object) -> None:
    dados = _valido()
    dados["to_Item"] = valor
    assert _um_erro(dados) == ("to_Item", ErrorCode.INVALID_TYPE, {"tipo": "lista"})


def test_elemento_de_lista_precisa_ser_objeto() -> None:
    dados = _valido()
    dados["to_Partner"].append("Y3")
    assert _um_erro(dados) == ("to_Partner[2]", ErrorCode.INVALID_TYPE, {"tipo": "objeto"})


def test_contract_criar_exige_mapping() -> None:
    with pytest.raises(TypeError) as exc:
        Contract.criar([])  # type: ignore[arg-type]
    assert str(exc.value) == "Contract.criar espera um Mapping (payload do contrato)"


# ---- Escala e precisao (sem arredondar) --------------------------------------


@pytest.mark.parametrize(
    ("caminho", "valor", "casas"),
    [
        (("to_Item", 0, "RequestedQuantity"), "1.2345", 3),
        (("to_FormPag", 0, "Porcentagem"), "33.33341", 4),
        (("to_FormPag", 0, "Valor"), "7766.521", 2),
        (("to_PricingElement", 0, "ConditionRateValue"), "1.0000000001", 9),
    ],
)
def test_escala_acima_da_permitida_e_erro_nunca_arredonda(
    caminho: tuple[str, int, str], valor: str, casas: int
) -> None:
    lista, i, nome = caminho
    dados = _valido()
    dados[lista][i][nome] = Decimal(valor)
    assert _um_erro(dados) == (f"{lista}[{i}].{nome}", ErrorCode.DECIMAL_SCALE, {"max": casas})


def test_zeros_a_direita_nao_contam_como_escala() -> None:
    dados = _valido()
    dados["to_Item"][0]["RequestedQuantity"] = Decimal("1.2300000")
    q = Contract.criar(dados).items[0].requested_quantity
    assert q == Decimal("1.23")
    assert str(q) == "1.230"


@pytest.mark.parametrize(
    ("caminho", "limite_ok", "limite_estourado", "digitos"),
    [
        (("to_Item", 0, "RequestedQuantity"), "999999999999.999", "1000000000000", 12),
        (("to_FormPag", 0, "Valor"), "9999999999999.99", "10000000000000", 13),
        (("to_PricingElement", 0, "ConditionRateValue"), "99999999999999", "100000000000000", 14),
    ],
)
def test_precisao_do_metadata(
    caminho: tuple[str, int, str], limite_ok: str, limite_estourado: str, digitos: int
) -> None:
    lista, i, nome = caminho
    dados = _valido()
    dados[lista][i][nome] = Decimal(limite_ok)
    if nome == "Valor":
        dados["to_FormPag"][i]["Porcentagem"] = Decimal("33.3334")
    Contract.criar(dados)
    dados[lista][i][nome] = Decimal(limite_estourado)
    assert _um_erro(dados) == (
        f"{lista}[{i}].{nome}",
        ErrorCode.DECIMAL_PRECISION,
        {"max": digitos},
    )


@pytest.mark.parametrize("valor", ["0", "-1", "-0.001"])
def test_quantidade_precisa_ser_positiva(valor: str) -> None:
    dados = _valido()
    dados["to_Item"][0]["RequestedQuantity"] = Decimal(valor)
    assert _um_erro(dados) == ("to_Item[0].RequestedQuantity", ErrorCode.MUST_BE_POSITIVE, {})


@pytest.mark.parametrize("valor", [0, -1])
def test_parcela_precisa_ser_positiva(valor: int) -> None:
    dados = _valido()
    dados["to_FormPag"][0]["Parcela"] = valor
    assert _um_erro(dados) == ("to_FormPag[0].Parcela", ErrorCode.MUST_BE_POSITIVE, {})


@pytest.mark.parametrize("campo", ["Porcentagem", "Valor"])
@pytest.mark.parametrize("valor", ["0", "-0.01", "-100"])
def test_porcentagem_e_valor_da_parcela_precisam_ser_positivos(campo: str, valor: str) -> None:
    """Defesa em profundidade: calcular_parcelas ja garante > 0; o VO tambem barra."""
    dados = _valido()
    dados["to_FormPag"][2][campo] = Decimal(valor)
    assert _um_erro(dados) == (f"to_FormPag[2].{campo}", ErrorCode.MUST_BE_POSITIVE, {})


@pytest.mark.parametrize(("campo", "minimo"), [("Porcentagem", "0.0001"), ("Valor", "0.01")])
def test_menor_parcela_positiva_e_aceita(campo: str, minimo: str) -> None:
    dados = _valido()
    dados["to_FormPag"][0][campo] = Decimal(minimo)
    Contract.criar(dados)


@pytest.mark.parametrize("valor", ["-1.5", "0", "-0.000000001", "1164.98"])
def test_condition_rate_value_aceita_qualquer_sinal(valor: str) -> None:
    """Descontos/abatimentos podem ser negativos. TODO(decisao #14): quais ConditionType."""
    dados = _valido()
    dados["to_PricingElement"][0]["ConditionRateValue"] = Decimal(valor)
    dados["to_Item"][0]["to_PricingElement"][0]["ConditionRateValue"] = Decimal(valor)
    c = Contract.criar(dados)
    assert c.pricing[0].condition_rate_value == Decimal(valor)
    assert c.items[0].pricing[0].condition_rate_value == Decimal(valor)


# ---- Campos que o cliente nao controla ---------------------------------------


@pytest.mark.parametrize(
    ("onde", "chave"),
    [
        ((), "StatusBlock"),
        ((), "SalesContract"),
        (("to_Item", 0), "SalesContractItem"),
        (("to_PricingElement", 0), "ConditionUUID"),
        ((), "SalesContractTyp"),  # erro de digitacao nao passa em silencio
    ],
)
def test_campo_desconhecido_e_rejeitado(onde: tuple[str, int] | tuple[()], chave: str) -> None:
    dados = _valido()
    alvo: dict[str, Any] = dados[onde[0]][onde[1]] if onde else dados
    alvo[chave] = "06"
    prefixo = f"{onde[0]}[{onde[1]}]." if onde else ""
    assert _um_erro(dados) == (f"{prefixo}{chave}", ErrorCode.UNKNOWN_FIELD, {})


# ---- Parceiro precisa de ao menos um identificador ---------------------------


def test_parceiro_sem_nenhum_identificador() -> None:
    dados = _valido()
    for k in ("Customer", "Supplier", "Personnel", "ContactPerson"):
        dados["to_Partner"][1][k] = " "
    assert _um_erro(dados) == ("to_Partner[1]", ErrorCode.PARTNER_IDENTIFIER_REQUIRED, {})


@pytest.mark.parametrize("identificador", ["Customer", "Supplier", "Personnel", "ContactPerson"])
def test_parceiro_com_um_identificador_basta(identificador: str) -> None:
    dados = _valido()
    for k in ("Customer", "Supplier", "Personnel", "ContactPerson"):
        dados["to_Partner"][0][k] = ""
    dados["to_Partner"][0][identificador] = "123"
    Contract.criar(dados)


# ---- Imutabilidade e invariantes de tipo -------------------------------------


def test_vos_sao_frozen() -> None:
    c = Contract.criar(_valido())
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.header.sold_to_party = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.items[0].material = "x"  # type: ignore[misc]
    assert isinstance(c.items, tuple)
    assert isinstance(c.items[0].pricing, tuple)


@pytest.mark.parametrize(
    ("cls", "campo", "valor_errado"),
    [
        (Item, "requested_quantity", 1.0),
        (Installment, "data", "2026-01-01"),
        (Installment, "parcela", True),
        (Partner, "customer", None),
        (PricingElement, "condition_rate_value", 1),
        (Text, "language", 1),
        (Header, "customer_purchase_order_date", datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_post_init_so_checa_tipo_de_uso_programatico(
    cls: type, campo: str, valor_errado: object
) -> None:
    c = Contract.criar(_valido())
    original = {
        Item: c.items[0],
        Installment: c.installments[0],
        Partner: c.partners[0],
        PricingElement: c.pricing[0],
        Text: c.texts[0],
        Header: c.header,
    }[cls]
    with pytest.raises(TypeError) as exc:
        dataclasses.replace(original, **{campo: valor_errado})
    assert str(exc.value).startswith(f"{cls.__name__}.{campo}: tipo invalido")


def test_post_init_do_contract_exige_header_e_tuplas_de_vo() -> None:
    c = Contract.criar(_valido())
    with pytest.raises(TypeError) as exc:
        dataclasses.replace(c, header=None)  # type: ignore[arg-type]
    assert str(exc.value) == "Contract.header: tipo invalido (esperado Header)"
    with pytest.raises(TypeError) as exc:
        dataclasses.replace(c, items=list(c.items))  # type: ignore[arg-type]
    assert str(exc.value) == "Contract.items: tipo invalido (esperado tuple[Item])"
    with pytest.raises(TypeError) as exc:
        dataclasses.replace(c.items[0], pricing=(c.partners[0],))  # type: ignore[arg-type]
    assert str(exc.value) == "Item.pricing: tipo invalido (esperado tuple[PricingElement])"
