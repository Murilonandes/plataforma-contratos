"""Parser das respostas do ``CriaContrato`` (Tarefa 2.6).

Exemplos sinteticos no formato de erro OData V4 do RAP. As fixtures reais
(anonimizadas) de DEV entram em ``tests/contract/`` depois do smoke
(``TODO(decisao #3)``).
"""

from __future__ import annotations

import json

import pytest

from app.application.ports import MensagemSap, RespostaSap
from app.infrastructure.sap.respostas import (
    ErroSap,
    RespostaInvalida,
    Sucesso,
    ler_avisos,
    ler_erro,
    ler_sucesso,
    mapear_target,
)


def _json(obj: object) -> bytes:
    return json.dumps(obj).encode()


def _r201(corpo: bytes, headers: dict[str, str] | None = None) -> RespostaSap:
    return RespostaSap(status=201, headers=headers or {}, corpo=corpo)


# ---- Erro ----------------------------------------------------------------------------------


def test_erro_com_details_vira_lista_de_mensagens() -> None:
    corpo = _json(
        {
            "error": {
                "code": "ZSD/001",
                "message": "Contrato com erros",
                "details": [
                    {"code": "V1/390", "message": "Cliente bloqueado", "target": "SoldToParty"},
                    {
                        "code": "V1/123",
                        "message": "Material inexistente",
                        "target": "to_Item(SalesContract='',SalesContractItem='000010')/Material",
                    },
                    {"code": "V1/999", "message": "Sem alvo"},
                ],
            }
        }
    )
    assert ler_erro(corpo) == ErroSap(
        code="ZSD/001",
        message="Contrato com erros",
        tem_details=True,
        mensagens=(
            MensagemSap(code="ZSD/001", message="Contrato com erros", target=None, path=None),
            MensagemSap(
                code="V1/390", message="Cliente bloqueado", target="SoldToParty", path="SoldToParty"
            ),
            MensagemSap(
                code="V1/123",
                message="Material inexistente",
                target="to_Item(SalesContract='',SalesContractItem='000010')/Material",
                path=None,
            ),
            MensagemSap(code="V1/999", message="Sem alvo", target=None, path=None),
        ),
    )


def test_erro_com_target_no_topo_e_sem_details() -> None:
    corpo = _json({"error": {"code": "X", "message": "m", "target": "SalesOrganization"}})
    erro = ler_erro(corpo)
    assert erro is not None
    assert erro.tem_details is False
    assert erro.mensagens == (
        MensagemSap(code="X", message="m", target="SalesOrganization", path="SalesOrganization"),
    )


@pytest.mark.parametrize("details", [[], None, "texto", {"code": "x"}, [1, "a", None]])
def test_details_vazio_ou_fora_do_formato_nao_conta_como_details(details: object) -> None:
    erro = ler_erro(_json({"error": {"code": "C", "message": "M", "details": details}}))
    assert erro is not None
    assert erro.tem_details is False
    assert erro.mensagens == (MensagemSap(code="C", message="M", target=None, path=None),)


def test_detail_com_campos_fora_do_tipo_vira_texto_e_vazio_vira_none() -> None:
    corpo = _json(
        {
            "error": {
                "code": 7,
                "message": None,
                "details": [{"code": 1, "message": 2, "target": ""}],
            }
        }
    )
    assert ler_erro(corpo) == ErroSap(
        code="7",
        message="",
        tem_details=True,
        mensagens=(
            MensagemSap(code="7", message="", target=None, path=None),
            MensagemSap(code="1", message="2", target=None, path=None),
        ),
    )


@pytest.mark.parametrize(
    "corpo",
    [
        b"",
        b"<html>erro</html>",
        b"\xff\xfe",
        b"[]",
        b'"texto"',
        b"{}",
        b'{"error": "texto"}',
        b'{"error": null}',
        b'{"x":NaN}',
    ],
)
def test_corpo_que_nao_e_erro_odata_devolve_none(corpo: bytes) -> None:
    assert ler_erro(corpo) is None


# ---- Target -> path -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "path"),
    [
        ("SoldToParty", "SoldToParty"),
        ("/SoldToParty", "SoldToParty"),
        ("PedidoSysFertil", "PedidoSysFertil"),
        # campos do cabecalho so; navegacao com chave do SAP fica crua (TODO(decisao #3))
        ("to_Item(SalesContract='',SalesContractItem='000010')/Material", None),
        ("to_Item/Material", None),
        ("Material", None),  # campo de item sem navegacao: ambiguo
        ("CampoQueNaoExiste", None),
        ("StatusBlock", None),  # constante do mapper, nao e campo do formulario
        ("SalesContract", None),  # Computed
        ("", None),
        (None, None),
        (" SoldToParty", None),
    ],
)
def test_mapear_target(target: str | None, path: str | None) -> None:
    assert mapear_target(target) == path


# ---- Sucesso --------------------------------------------------------------------------------


def test_201_le_sales_contract_e_avisos_do_header() -> None:
    avisos = [
        {"code": "V1/555", "message": "Preco abaixo da tabela", "target": "SoldToParty"},
        {"code": "V4/1", "message": "Aviso sem alvo", "numericSeverity": 2},
    ]
    r = _r201(
        _json({"@odata.context": "$metadata#CriaContrato/$entity", "SalesContract": "40001234"}),
        {"sap-messages": json.dumps(avisos)},
    )
    assert ler_sucesso(r) == Sucesso(
        sales_contract="40001234",
        avisos=(
            MensagemSap(
                code="V1/555",
                message="Preco abaixo da tabela",
                target="SoldToParty",
                path="SoldToParty",
            ),
            MensagemSap(code="V4/1", message="Aviso sem alvo", target=None, path=None),
        ),
    )


def test_201_sem_header_de_avisos() -> None:
    assert ler_sucesso(_r201(_json({"SalesContract": "40001234"}))) == Sucesso(
        sales_contract="40001234", avisos=()
    )


@pytest.mark.parametrize(
    ("corpo", "mensagem"),
    [
        (b"", "201 sem JSON valido"),
        (b"nao-json", "201 sem JSON valido"),
        (b"\xff", "201 sem JSON valido"),
        (b'{"SalesContract":NaN}', "201 sem JSON valido"),
        (b"[]", "201 com JSON que nao e objeto"),
        (b'"40001234"', "201 com JSON que nao e objeto"),
        (b"{}", "201 sem SalesContract"),
        (b'{"SalesContract":null}', "201 sem SalesContract"),
        (b'{"SalesContract":40001234}', "201 com SalesContract que nao e texto"),
        (b'{"SalesContract":true}', "201 com SalesContract que nao e texto"),
    ],
)
def test_201_fora_do_formato_levanta_resposta_invalida(corpo: bytes, mensagem: str) -> None:
    with pytest.raises(RespostaInvalida) as exc:
        ler_sucesso(_r201(corpo))
    assert str(exc.value) == mensagem


def test_ler_sucesso_so_aceita_201() -> None:
    with pytest.raises(RespostaInvalida) as exc:
        ler_sucesso(RespostaSap(status=200, headers={}, corpo=b'{"SalesContract":"1"}'))
    assert str(exc.value) == "ler_sucesso chamado com status 200"


def test_sales_contract_passa_cru_quem_normaliza_e_a_transition() -> None:
    # letras, mais de 10 digitos e so zeros sao recusados pela transition (D9), nao aqui
    for bruto in ("ABC", "00000000000", "0000000000", " 40001234 "):
        assert ler_sucesso(_r201(_json({"SalesContract": bruto}))).sales_contract == bruto


# ---- sap-messages (so informativo: nunca derruba um 201) --------------------------------------


@pytest.mark.parametrize(
    "valor",
    ["", "nao-json", "{}", "123", "[1, null]", "[{}]", "[NaN]"],
)
def test_sap_messages_fora_do_formato_nao_derruba_e_ignora_o_que_nao_e_mensagem(
    valor: str,
) -> None:
    assert ler_avisos({"sap-messages": valor}) == ()


def test_sap_messages_objeto_unico_e_header_com_caixa_diferente() -> None:
    assert ler_avisos({"SAP-Messages": '{"code":"A","message":"b"}'}) == (
        MensagemSap(code="A", message="b", target=None, path=None),
    )
