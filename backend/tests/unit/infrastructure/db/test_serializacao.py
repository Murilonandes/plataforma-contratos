"""Serializacao do snapshot (D6') e do rascunho ``entrada`` para JSONB.

Snapshot: ``Contract`` -> JSON canonico (decimal como string de escala fixa,
data ISO, formato OData de entrada) -> ``Contract.criar`` de volta, com
round-trip EXATO: ``desserializar(serializar(c)) == c``.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.contract import Contract
from app.infrastructure.db.serializacao import (
    contrato_de_json,
    contrato_para_json,
    entrada_para_json,
)
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada


def _contrato(entrada: dict[str, Any] | None = None) -> Contract:
    return Contract.criar(entrada if entrada is not None else payload_exemplo_como_entrada())


# ---- Formato canonico ----------------------------------------------------------------


def test_decimal_sai_como_string_de_escala_fixa() -> None:
    j = contrato_para_json(_contrato())
    assert j["to_Item"][0]["RequestedQuantity"] == "1.000"
    assert j["to_Item"][0]["to_PricingElement"][0]["ConditionRateValue"] == "1164.980000000"
    assert j["to_FormPag"][0]["Porcentagem"] == "33.3334"
    assert j["to_FormPag"][0]["Valor"] == "7766.52"


def test_data_sai_iso_e_data_ausente_e_omitida() -> None:
    entrada = payload_exemplo_como_entrada()
    del entrada["CustomerPurchaseOrderDate"]
    j = contrato_para_json(_contrato(entrada))
    assert j["SalesContractValidityEndDate"] == "2027-08-05"
    assert "CustomerPurchaseOrderDate" not in j


def test_json_canonico_so_tem_tipos_json_e_nao_tem_status_block() -> None:
    j = contrato_para_json(_contrato())
    texto = json.dumps(j)  # nada de Decimal/date: json.dumps puro aceita
    assert json.loads(texto) == j
    assert "StatusBlock" not in j
    assert "SalesContract" not in j


def test_json_nunca_tem_float() -> None:
    def varre(no: object) -> None:
        assert not isinstance(no, float)
        if isinstance(no, dict):
            for v in no.values():
                varre(v)
        elif isinstance(no, list):
            for v in no:
                varre(v)

    varre(contrato_para_json(_contrato()))


# ---- Round-trip ----------------------------------------------------------------------


def test_round_trip_do_payload_de_exemplo() -> None:
    c = _contrato()
    assert contrato_de_json(contrato_para_json(c)) == c


def test_round_trip_preserva_a_escala_dos_decimais() -> None:
    c = _contrato()
    volta = contrato_de_json(contrato_para_json(c))
    assert str(volta.items[0].requested_quantity) == str(c.items[0].requested_quantity)
    assert str(volta.installments[2].valor) == "7766.51"


def test_desserializar_nao_altera_o_json() -> None:
    j = contrato_para_json(_contrato())
    original = copy.deepcopy(j)
    contrato_de_json(j)
    assert j == original


@pytest.mark.parametrize(
    ("caminho", "valor"),
    [
        (("to_Item", 0, "RequestedQuantity"), 1),  # numero JSON: recusado (sem float)
        (("to_Item", 0, "RequestedQuantity"), 1.0),
        (("to_FormPag", 0, "Data"), 20260904),
    ],
)
def test_desserializar_recusa_tipo_fora_do_formato_canonico(
    caminho: tuple[str, int, str], valor: object
) -> None:
    j = contrato_para_json(_contrato())
    lista, i, campo = caminho
    j[lista][i][campo] = valor
    with pytest.raises(TypeError) as exc:
        contrato_de_json(j)
    assert str(exc.value) == f"snapshot: {lista}[{i}].{campo} fora do formato canonico"


_TEXTO = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E), min_size=1, max_size=40
)


@given(
    textos=st.lists(_TEXTO, min_size=1, max_size=3),
    quantidades=st.lists(
        st.decimals(min_value=Decimal("0.001"), max_value=Decimal("999999"), places=3),
        min_size=1,
        max_size=3,
    ),
    taxa=st.decimals(min_value=Decimal("-99999"), max_value=Decimal("99999"), places=9),
    nota=_TEXTO,
    sem_data=st.booleans(),
)
def test_propriedade_round_trip_exato(
    textos: list[str],
    quantidades: list[Decimal],
    taxa: Decimal,
    nota: str,
    sem_data: bool,
) -> None:
    entrada = payload_exemplo_como_entrada()
    base = entrada["to_Item"][0]
    entrada["to_Item"] = []
    for texto, qtd in zip(textos, quantidades, strict=False):
        item = copy.deepcopy(base)
        item["SalesContractItemText"] = texto
        item["RequestedQuantity"] = qtd
        entrada["to_Item"].append(item)
    entrada["to_PricingElement"][0]["ConditionRateValue"] = taxa
    entrada["NotaInternaCli"] = nota
    if sem_data:
        del entrada["SalesContractValidityEndDate"]
    c = Contract.criar(entrada)
    j = contrato_para_json(c)
    assert contrato_de_json(json.loads(json.dumps(j))) == c


# ---- Rascunho (entrada) ------------------------------------------------------------


def test_entrada_vira_json_com_decimal_e_data_como_texto() -> None:
    entrada = {
        "SalesOrganization": "BRF1",
        "total": Decimal("23299.550"),
        "datas": [date(2026, 9, 4)],
        "pesos": (1, 2),
        "nada": None,
        "flag": True,
        "to_Item": [{"RequestedQuantity": Decimal("-0.5")}],
    }
    assert entrada_para_json(entrada) == {
        "SalesOrganization": "BRF1",
        "total": "23299.550",
        "datas": ["2026-09-04"],
        "pesos": [1, 2],
        "nada": None,
        "flag": True,
        "to_Item": [{"RequestedQuantity": "-0.5"}],
    }


@pytest.mark.parametrize(
    "valor",
    [1.5, datetime(2026, 1, 1, tzinfo=UTC), Decimal("NaN"), object(), {1: "chave nao str"}],
)
def test_entrada_recusa_o_que_nao_tem_representacao_segura(valor: object) -> None:
    with pytest.raises(TypeError):
        entrada_para_json({"x": valor})
