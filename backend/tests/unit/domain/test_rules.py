"""Regras de negocio inter-campo (``rules.py``).

Funcao pura sobre dados ja validados campo a campo; o ``Contract.criar`` chama
com o mesmo ``ErrorCollector``, entao o contrato continua levantando UMA vez,
com erros de campo e de regra juntos.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.contract import Contract
from app.domain.errors import DomainValidationError, ErrorCode, FieldError
from app.domain.rules import validar_regras
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada


def _p(*funcoes: str) -> list[tuple[str, str]]:
    return [(f"to_Partner[{i}]", f) for i, f in enumerate(funcoes)]


def _resumo(erros: tuple[FieldError, ...]) -> list[tuple[str, ErrorCode, dict[str, object]]]:
    return [(e.path, e.code, dict(e.params)) for e in erros]


# ---- validar_regras (unidade) ------------------------------------------------


def test_contrato_valido_nao_tem_erro() -> None:
    assert validar_regras(parceiros=_p("Y1", "Y2"), itens_recebidos=1) == ()


def test_parceiro_duplicado_aponta_a_segunda_ocorrencia() -> None:
    erros = validar_regras(parceiros=_p("Y1", "Y2", "Y1"), itens_recebidos=1)
    assert _resumo(erros) == [
        (
            "to_Partner[2].PartnerFunction",
            ErrorCode.DUPLICATE_PARTNER_FUNCTION,
            {"funcao": "Y1"},
        )
    ]
    assert erros[0].message == "parceiro duplicado para funcao 'Y1'"


def test_toda_repeticao_depois_da_primeira_e_erro_na_ordem_do_payload() -> None:
    erros = validar_regras(parceiros=_p("Y2", "Y1", "Y2", "Y1", "Y2"), itens_recebidos=1)
    assert [(e.path, e.params["funcao"]) for e in erros] == [
        ("to_Partner[2].PartnerFunction", "Y2"),
        ("to_Partner[3].PartnerFunction", "Y1"),
        ("to_Partner[4].PartnerFunction", "Y2"),
    ]


def test_path_vem_do_elemento_e_nao_da_posicao_na_lista() -> None:
    """Elemento invalido e pulado antes das regras: o indice real vem no path."""
    parceiros = [("to_Partner[0]", "Y1"), ("to_Partner[3]", "Y1")]
    erros = validar_regras(parceiros=parceiros, itens_recebidos=1)
    assert [e.path for e in erros] == ["to_Partner[3].PartnerFunction"]


def test_funcao_vazia_nao_e_duplicada() -> None:
    """Vazia ja e erro ``required`` no campo; a regra nao repete o aviso."""
    assert validar_regras(parceiros=_p("", "", "Y1"), itens_recebidos=1) == ()


def test_comparacao_e_exata_sem_normalizar_caixa() -> None:
    """PartnerFunction nao e IsUpperCase no metadata: o dominio nao normaliza."""
    assert validar_regras(parceiros=_p("Y1", "y1"), itens_recebidos=1) == ()


def test_sem_parceiros_nao_e_erro_de_regra() -> None:
    assert validar_regras(parceiros=[], itens_recebidos=1) == ()


@pytest.mark.parametrize("itens", [1, 2, 50])
def test_ao_menos_um_item_basta(itens: int) -> None:
    assert validar_regras(parceiros=[], itens_recebidos=itens) == ()


def test_contrato_sem_item() -> None:
    erros = validar_regras(parceiros=[], itens_recebidos=0)
    assert _resumo(erros) == [("to_Item", ErrorCode.MIN_ITEMS, {"min": 1})]
    assert erros[0].message == "campo 'to_Item' precisa de ao menos 1 elemento(s)"


def test_itens_desconhecidos_nao_geram_min_items() -> None:
    """``to_Item`` que nem e lista ja deu ``invalid_type``; nao empilha outro erro."""
    assert validar_regras(parceiros=[], itens_recebidos=None) == ()


def test_erros_de_regra_acumulam_itens_antes_de_parceiros() -> None:
    erros = validar_regras(parceiros=_p("Y1", "Y1"), itens_recebidos=0)
    assert [e.code for e in erros] == [
        ErrorCode.MIN_ITEMS,
        ErrorCode.DUPLICATE_PARTNER_FUNCTION,
    ]


# ---- Integracao com Contract.criar (uma excecao so) --------------------------


def _erros(dados: dict[str, Any]) -> list[tuple[str, ErrorCode, dict[str, object]]]:
    with pytest.raises(DomainValidationError) as exc:
        Contract.criar(dados)
    return _resumo(exc.value.errors)


def test_criar_rejeita_parceiro_duplicado() -> None:
    dados = payload_exemplo_como_entrada()
    dados["to_Partner"][1]["PartnerFunction"] = "Y1"
    assert _erros(dados) == [
        ("to_Partner[1].PartnerFunction", ErrorCode.DUPLICATE_PARTNER_FUNCTION, {"funcao": "Y1"})
    ]


def test_criar_compara_funcao_depois_do_strip() -> None:
    dados = payload_exemplo_como_entrada()
    dados["to_Partner"][1]["PartnerFunction"] = " Y1 "
    assert _erros(dados) == [
        ("to_Partner[1].PartnerFunction", ErrorCode.DUPLICATE_PARTNER_FUNCTION, {"funcao": "Y1"})
    ]


def test_criar_usa_o_indice_real_quando_ha_elemento_invalido() -> None:
    dados = payload_exemplo_como_entrada()
    dados["to_Partner"].insert(1, "nao sou objeto")
    dados["to_Partner"][2]["PartnerFunction"] = "Y1"
    assert _erros(dados) == [
        ("to_Partner[1]", ErrorCode.INVALID_TYPE, {"tipo": "objeto"}),
        ("to_Partner[2].PartnerFunction", ErrorCode.DUPLICATE_PARTNER_FUNCTION, {"funcao": "Y1"}),
    ]


@pytest.mark.parametrize("sem_itens", ["ausente", None, [], ()])
def test_criar_rejeita_contrato_sem_item(sem_itens: object) -> None:
    dados = payload_exemplo_como_entrada()
    if sem_itens == "ausente":
        del dados["to_Item"]
    else:
        dados["to_Item"] = sem_itens
    assert _erros(dados) == [("to_Item", ErrorCode.MIN_ITEMS, {"min": 1})]


def test_criar_conta_item_invalido_como_recebido() -> None:
    """Item que nao e objeto ja e erro; nao vira tambem 'sem itens'."""
    dados = payload_exemplo_como_entrada()
    dados["to_Item"] = ["x"]
    assert _erros(dados) == [("to_Item[0]", ErrorCode.INVALID_TYPE, {"tipo": "objeto"})]


def test_criar_to_item_que_nao_e_lista_da_so_invalid_type() -> None:
    dados = payload_exemplo_como_entrada()
    dados["to_Item"] = "x"
    assert _erros(dados) == [("to_Item", ErrorCode.INVALID_TYPE, {"tipo": "lista"})]


def test_criar_junta_erro_de_campo_e_de_regra_numa_excecao_so() -> None:
    dados = payload_exemplo_como_entrada()
    dados["SoldToParty"] = ""
    dados["to_Partner"][1]["PartnerFunction"] = "Y1"
    dados["to_Item"] = []
    assert _erros(dados) == [
        ("SoldToParty", ErrorCode.REQUIRED, {}),
        ("to_Item", ErrorCode.MIN_ITEMS, {"min": 1}),
        ("to_Partner[1].PartnerFunction", ErrorCode.DUPLICATE_PARTNER_FUNCTION, {"funcao": "Y1"}),
    ]
