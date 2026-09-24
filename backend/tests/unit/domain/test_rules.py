"""Regras de negocio inter-campo (``rules.py``).

Funcao pura sobre dados ja validados campo a campo; o ``Contract.criar`` chama
com o mesmo ``ErrorCollector``, entao o contrato continua levantando UMA vez,
com erros de campo e de regra juntos.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.domain.contract import Contract
from app.domain.errors import DomainValidationError, ErrorCode, FieldError
from app.domain.politica import POLITICA_PADRAO, PoliticaSalesOrg
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


# ---- to_FormPag: o dominio nao confia em quem montou (M1) --------------------


def _parcelas(dados: dict[str, Any]) -> list[dict[str, Any]]:
    parcelas: list[dict[str, Any]] = dados["to_FormPag"]
    return parcelas


def _n_parcelas(n: int, *, primeira_pct: str, demais_pct: str) -> list[dict[str, Any]]:
    return [
        {
            "Parcela": i + 1,
            "Porcentagem": Decimal(primeira_pct if i == 0 else demais_pct),
            "Valor": Decimal("1.00"),
            "Data": date(2026, 1, 1) + timedelta(days=i),
            "FormPag": "K",
            "TransactionCurrency": "BRL",
        }
        for i in range(n)
    ]


def test_payload_exemplo_tem_parcelas_validas() -> None:
    assert len(Contract.criar(payload_exemplo_como_entrada()).installments) == 3


_SO_MOEDA = {"BRF1": PoliticaSalesOrg(moedas=frozenset({"BRL"}))}


def test_contrato_sem_parcelas_e_aceito_sem_obrigatoriedade_de_negocio() -> None:
    dados = payload_exemplo_como_entrada()
    dados["to_FormPag"] = []
    assert Contract.criar(dados, politica=_SO_MOEDA).installments == ()


@pytest.mark.parametrize("campo", ["Porcentagem", "Valor"])
@pytest.mark.parametrize("ausente", ["chave", None])
def test_porcentagem_e_valor_sao_obrigatorios(campo: str, ausente: str | None) -> None:
    dados = payload_exemplo_como_entrada()
    if ausente == "chave":
        del _parcelas(dados)[1][campo]
    else:
        _parcelas(dados)[1][campo] = None
    assert _erros(dados) == [(f"to_FormPag[1].{campo}", ErrorCode.REQUIRED, {})]


def test_parcelas_fora_de_ordem() -> None:
    dados = payload_exemplo_como_entrada()
    _parcelas(dados)[1]["Parcela"] = 3
    _parcelas(dados)[2]["Parcela"] = 2
    assert _erros(dados) == [
        ("to_FormPag[1].Parcela", ErrorCode.INSTALLMENT_OUT_OF_SEQUENCE, {"esperado": 2}),
        ("to_FormPag[2].Parcela", ErrorCode.INSTALLMENT_OUT_OF_SEQUENCE, {"esperado": 3}),
    ]


def test_parcelas_que_nao_comecam_em_1() -> None:
    dados = payload_exemplo_como_entrada()
    for i, p in enumerate(_parcelas(dados)):
        p["Parcela"] = i + 2
    assert [e[0] for e in _erros(dados)] == [
        "to_FormPag[0].Parcela",
        "to_FormPag[1].Parcela",
        "to_FormPag[2].Parcela",
    ]


def test_parcela_repetida() -> None:
    dados = payload_exemplo_como_entrada()
    _parcelas(dados)[2]["Parcela"] = 2
    assert _erros(dados) == [
        ("to_FormPag[2].Parcela", ErrorCode.INSTALLMENT_OUT_OF_SEQUENCE, {"esperado": 3}),
    ]


def test_parcela_com_erro_de_campo_nao_gera_erro_de_sequencia() -> None:
    dados = payload_exemplo_como_entrada()
    _parcelas(dados)[1]["Parcela"] = 0
    assert _erros(dados) == [("to_FormPag[1].Parcela", ErrorCode.MUST_BE_POSITIVE, {})]


def test_elemento_invalido_em_to_formpag_nao_gera_erro_de_sequencia() -> None:
    dados = payload_exemplo_como_entrada()
    _parcelas(dados).insert(1, "x")
    assert _erros(dados) == [("to_FormPag[1]", ErrorCode.INVALID_TYPE, {"tipo": "objeto"})]


@pytest.mark.parametrize(("ultima", "soma"), [("33.3334", "100.0001"), ("33.3332", "99.9999")])
def test_soma_das_porcentagens_precisa_ser_100(ultima: str, soma: str) -> None:
    dados = payload_exemplo_como_entrada()
    _parcelas(dados)[2]["Porcentagem"] = Decimal(ultima)
    assert _erros(dados) == [("to_FormPag", ErrorCode.INSTALLMENT_PERCENT_SUM, {"soma": soma})]


def test_soma_e_exata_com_escala_4() -> None:
    dados = payload_exemplo_como_entrada()
    dados["to_FormPag"] = _n_parcelas(1, primeira_pct="100", demais_pct="0")
    assert Contract.criar(dados).installments[0].porcentagem == Decimal("100.0000")


def test_soma_nao_e_conferida_com_porcentagem_invalida() -> None:
    dados = payload_exemplo_como_entrada()
    _parcelas(dados)[2]["Porcentagem"] = Decimal("-1")
    assert _erros(dados) == [("to_FormPag[2].Porcentagem", ErrorCode.MUST_BE_POSITIVE, {})]


def test_datas_das_parcelas_estritamente_crescentes() -> None:
    dados = payload_exemplo_como_entrada()
    parcelas = _parcelas(dados)
    parcelas[1]["Data"] = parcelas[0]["Data"]
    parcelas[2]["Data"] = parcelas[0]["Data"] - timedelta(days=1)
    assert _erros(dados) == [
        ("to_FormPag[1].Data", ErrorCode.DATES_NOT_INCREASING, {}),
        ("to_FormPag[2].Data", ErrorCode.DATES_NOT_INCREASING, {}),
    ]


def test_data_ausente_nao_e_comparada() -> None:
    """Data e opcional (Edm.Date nullable); so pares consecutivos presentes sao comparados."""
    dados = payload_exemplo_como_entrada()
    parcelas = _parcelas(dados)
    del parcelas[1]["Data"]
    parcelas[2]["Data"] = parcelas[0]["Data"] + timedelta(days=1)
    assert Contract.criar(dados, politica=_SO_MOEDA).installments[1].data is None


def test_limite_de_36_parcelas() -> None:
    dados = payload_exemplo_como_entrada()
    dados["to_FormPag"] = _n_parcelas(36, primeira_pct="2.7770", demais_pct="2.7778")
    assert len(Contract.criar(dados).installments) == 36
    dados["to_FormPag"] = _n_parcelas(37, primeira_pct="2.7028", demais_pct="2.7027")
    assert _erros(dados) == [("to_FormPag", ErrorCode.MAX_ITEMS, {"max": 36})]


def test_violacoes_de_parcela_acumulam() -> None:
    dados = payload_exemplo_como_entrada()
    parcelas = _parcelas(dados)
    parcelas[0]["Parcela"] = 9
    parcelas[1]["Porcentagem"] = Decimal("1")
    parcelas[2]["Data"] = parcelas[1]["Data"]
    assert _erros(dados) == [
        ("to_FormPag[0].Parcela", ErrorCode.INSTALLMENT_OUT_OF_SEQUENCE, {"esperado": 1}),
        ("to_FormPag", ErrorCode.INSTALLMENT_PERCENT_SUM, {"soma": "67.6667"}),
        ("to_FormPag[2].Data", ErrorCode.DATES_NOT_INCREASING, {}),
    ]


# ---- Moeda por sales org (M2, TODO(decisao #11/#15)) -------------------------


def test_politica_padrao_e_brf1_so_brl_e_imutavel() -> None:
    assert dict(POLITICA_PADRAO) == {
        "BRF1": PoliticaSalesOrg(
            moedas=frozenset({"BRL"}),
            condicoes_com_parcelas=frozenset({"Z999"}),
            data_da_parcela_obrigatoria=True,
        )
    }
    with pytest.raises(TypeError):
        POLITICA_PADRAO["XX01"] = PoliticaSalesOrg(moedas=frozenset({"USD"}))  # type: ignore[index]


def test_moeda_fora_da_politica() -> None:
    dados = payload_exemplo_como_entrada()
    dados["TransactionCurrency"] = "USD"
    for el in (*dados["to_Item"], *dados["to_FormPag"]):
        el["TransactionCurrency"] = "USD"
    assert _erros(dados) == [
        (
            "TransactionCurrency",
            ErrorCode.CURRENCY_NOT_ALLOWED,
            {"moeda": "USD", "permitidas": "BRL"},
        )
    ]


def test_sales_org_sem_configuracao() -> None:
    dados = payload_exemplo_como_entrada()
    dados["SalesOrganization"] = "XX01"
    assert _erros(dados) == [
        ("SalesOrganization", ErrorCode.SALES_ORG_NOT_CONFIGURED, {"sales_org": "XX01"})
    ]


def test_politica_injetada_permite_outra_moeda() -> None:
    dados = payload_exemplo_como_entrada()
    dados["TransactionCurrency"] = "USD"
    for el in (*dados["to_Item"], *dados["to_FormPag"]):
        el["TransactionCurrency"] = "USD"
    politica = {"BRF1": PoliticaSalesOrg(moedas=frozenset({"BRL", "USD"}))}
    assert Contract.criar(dados, politica=politica).header.transaction_currency == "USD"


def test_permitidas_em_ordem_alfabetica_na_mensagem() -> None:
    dados = payload_exemplo_como_entrada()
    dados["TransactionCurrency"] = "JPY"
    for el in (*dados["to_Item"], *dados["to_FormPag"]):
        el["TransactionCurrency"] = "JPY"
    politica = {"BRF1": PoliticaSalesOrg(moedas=frozenset({"USD", "BRL", "EUR"}))}
    with pytest.raises(DomainValidationError) as exc:
        Contract.criar(dados, politica=politica)
    assert [dict(e.params) for e in exc.value.errors] == [
        {"moeda": "JPY", "permitidas": "BRL, EUR, USD"}
    ]


@pytest.mark.parametrize(("lista", "i"), [("to_Item", 0), ("to_FormPag", 0), ("to_FormPag", 2)])
def test_moeda_diferente_da_do_cabecalho(lista: str, i: int) -> None:
    dados = payload_exemplo_como_entrada()
    dados[lista][i]["TransactionCurrency"] = "USD"
    assert _erros(dados) == [
        (
            f"{lista}[{i}].TransactionCurrency",
            ErrorCode.CURRENCY_MISMATCH,
            {"esperado": "BRL", "recebido": "USD"},
        )
    ]


def test_moeda_do_item_vazia_herda_a_do_cabecalho() -> None:
    """TransactionCurrency do item nao e Mandatory no metadata: vazio e aceito."""
    dados = payload_exemplo_como_entrada()
    dados["to_Item"][0]["TransactionCurrency"] = ""
    assert Contract.criar(dados).items[0].transaction_currency == ""


def test_sem_moeda_no_cabecalho_nao_empilha_erro_de_moeda() -> None:
    dados = payload_exemplo_como_entrada()
    dados["TransactionCurrency"] = ""
    assert _erros(dados) == [("TransactionCurrency", ErrorCode.REQUIRED, {})]


def test_sem_sales_org_nao_empilha_erro_de_configuracao() -> None:
    dados = payload_exemplo_como_entrada()
    dados["SalesOrganization"] = ""
    assert _erros(dados) == [("SalesOrganization", ErrorCode.REQUIRED, {})]


def test_sales_org_sem_config_ainda_confere_divergencia_de_moeda() -> None:
    dados = payload_exemplo_como_entrada()
    dados["SalesOrganization"] = "XX01"
    dados["to_Item"][0]["TransactionCurrency"] = "USD"
    assert _erros(dados) == [
        ("SalesOrganization", ErrorCode.SALES_ORG_NOT_CONFIGURED, {"sales_org": "XX01"}),
        (
            "to_Item[0].TransactionCurrency",
            ErrorCode.CURRENCY_MISMATCH,
            {"esperado": "BRL", "recebido": "USD"},
        ),
    ]


@pytest.mark.parametrize(
    ("onde", "valor", "path"),
    [
        ((), ("SalesOrganization", "BRF12"), "SalesOrganization"),
        ((), ("TransactionCurrency", "BRLX"), "TransactionCurrency"),
        (("to_Item", 0), ("TransactionCurrency", "USDX"), "to_Item[0].TransactionCurrency"),
        (("to_FormPag", 1), ("TransactionCurrency", "USDX"), "to_FormPag[1].TransactionCurrency"),
    ],
)
def test_campo_de_moeda_com_erro_proprio_nao_empilha_regra(
    onde: tuple[Any, ...], valor: tuple[str, str], path: str
) -> None:
    dados = payload_exemplo_como_entrada()
    alvo: Any = dados
    for passo in onde:
        alvo = alvo[passo]
    alvo[valor[0]] = valor[1]
    assert [e[0] for e in _erros(dados)] == [path]


# ---- Parcelas obrigatorias de negocio (BRF1: condicao Z999, TODO(decisao #16)) ---


def test_politica_padrao_exige_parcelas_na_z999_e_data_em_cada_parcela() -> None:
    brf1 = POLITICA_PADRAO["BRF1"]
    assert brf1.condicoes_com_parcelas == frozenset({"Z999"})
    assert brf1.data_da_parcela_obrigatoria is True


@pytest.mark.parametrize("sem_parcelas", ["ausente", None, []])
def test_z999_sem_parcelas(sem_parcelas: object) -> None:
    dados = payload_exemplo_como_entrada()
    assert dados["CustomerPaymentTerms"] == "Z999"
    if sem_parcelas == "ausente":
        del dados["to_FormPag"]
    else:
        dados["to_FormPag"] = sem_parcelas
    assert _erros(dados) == [("to_FormPag", ErrorCode.MIN_ITEMS, {"min": 1})]


@pytest.mark.parametrize("condicao", ["Z001", ""])
def test_outra_condicao_aceita_contrato_sem_parcelas(condicao: str) -> None:
    dados = payload_exemplo_como_entrada()
    dados["CustomerPaymentTerms"] = condicao
    dados["to_FormPag"] = []
    assert Contract.criar(dados).installments == ()


@pytest.mark.parametrize("condicao", ["Z999", "Z001"])
def test_data_obrigatoria_em_toda_parcela_presente(condicao: str) -> None:
    dados = payload_exemplo_como_entrada()
    dados["CustomerPaymentTerms"] = condicao
    del _parcelas(dados)[1]["Data"]
    _parcelas(dados)[2]["Data"] = None
    assert _erros(dados) == [
        ("to_FormPag[1].Data", ErrorCode.REQUIRED, {}),
        ("to_FormPag[2].Data", ErrorCode.REQUIRED, {}),
    ]


def test_data_com_erro_de_tipo_nao_vira_required() -> None:
    dados = payload_exemplo_como_entrada()
    _parcelas(dados)[1]["Data"] = "2026-10-04"
    assert _erros(dados) == [("to_FormPag[1].Data", ErrorCode.INVALID_TYPE, {"tipo": "data"})]


def test_condicao_com_erro_propio_nao_empilha_min_items() -> None:
    dados = payload_exemplo_como_entrada()
    dados["CustomerPaymentTerms"] = "Z9999"  # > 4
    dados["to_FormPag"] = []
    assert _erros(dados) == [("CustomerPaymentTerms", ErrorCode.MAX_LENGTH, {"max": 4})]


def test_sales_org_sem_politica_nao_empilha_obrigatoriedade_de_parcela() -> None:
    dados = payload_exemplo_como_entrada()
    dados["SalesOrganization"] = "XX01"
    dados["to_FormPag"] = []
    assert _erros(dados) == [
        ("SalesOrganization", ErrorCode.SALES_ORG_NOT_CONFIGURED, {"sales_org": "XX01"})
    ]


def test_to_formpag_que_nao_e_lista_nao_empilha_min_items() -> None:
    dados = payload_exemplo_como_entrada()
    dados["to_FormPag"] = "x"
    assert _erros(dados) == [("to_FormPag", ErrorCode.INVALID_TYPE, {"tipo": "lista"})]
