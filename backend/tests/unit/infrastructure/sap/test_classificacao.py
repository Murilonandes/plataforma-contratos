"""Classificacao do resultado do SAP (Tarefa 2.7, D8). Funcao pura, no gate de mutacao.

Regra: "o POST pode ter saido?". As duas tabelas da §4 sao lidas do ``.md``
(``_referencias_arquitetura``) e conferidas contra o codigo.
"""

from __future__ import annotations

import json

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.application.ports import MensagemSap, RespostaSap
from app.domain.enums import ContractStatus as S
from app.domain.enums import TransitionEvent as E
from app.domain.states import MATRIZ
from app.infrastructure.sap.classificacao import (
    NEGOCIO_4XX,
    TECNICO_4XX,
    Classificacao,
    classificar_csrf,
    classificar_excecao_post,
    classificar_resposta_post,
)
from app.infrastructure.sap.client import CsrfIndisponivel, FalhaNoRefetchCsrf
from tests.unit.domain._referencias_arquitetura import (
    eventos_da_regra,
    linhas_transporte,
    status_da_regra,
)

_REQ = httpx.Request("POST", "https://s4-dev.example/x")


def _exc(nome: str) -> Exception:
    classe = getattr(httpx, nome)
    if issubclass(classe, httpx.RequestError):
        return classe("x", request=_REQ)  # type: ignore[no-any-return]
    return classe("x")  # type: ignore[no-any-return]


def _r(status: int, corpo: bytes = b"", headers: dict[str, str] | None = None) -> RespostaSap:
    return RespostaSap(status=status, headers=headers or {}, corpo=corpo)


def _erro(details: list[dict[str, str]] | None = None) -> bytes:
    erro: dict[str, object] = {"code": "C", "message": "M"}
    if details is not None:
        erro["details"] = details
    return json.dumps({"error": erro}).encode()


_COM_DETAILS = _erro([{"code": "D", "message": "detalhe", "target": "SoldToParty"}])


def _linha(prefixo: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    (linha,) = [x for x in linhas_transporte() if x.situacao.startswith(prefixo)]
    return linha.excecoes, tuple(c for c in linha.citados if c in E.__members__)


# ---- Tabela "Exceções de transporte" do .md --------------------------------------------------


def test_a_tabela_de_transporte_tem_as_linhas_esperadas() -> None:
    assert [x.situacao for x in linhas_transporte()] == [
        "CSRF fetch: qualquer falha",
        "CSRF fetch: `401`/`403`",
        "POST: conexão não estabeleceu",
        "envio do body falhou",
        "resposta não veio (timeout)",
        "resposta não veio (conexão)",
        "qualquer outra exceção",
    ]


def test_csrf_qualquer_excecao_da_tabela_e_nao_mapeada_viram_falha_antes_post() -> None:
    excecoes, (evento,) = _linha("CSRF fetch: qualquer falha")
    assert excecoes
    for exc in [*(_exc(n) for n in excecoes), ValueError("bug"), TimeoutError()]:
        assert classificar_csrf(exc, token_obtido=False) == Classificacao(
            evento=E(evento),
            detalhe={"fase": "csrf", "error_class": type(exc).__name__},
        )


@pytest.mark.parametrize(
    "prefixo",
    [
        "POST: conexão não estabeleceu",
        "envio do body falhou",
        "resposta não veio (timeout)",
        "resposta não veio (conexão)",
    ],
)
@pytest.mark.parametrize("marcador", [True, False])
def test_post_excecoes_da_tabela(prefixo: str, *, marcador: bool) -> None:
    excecoes, (evento,) = _linha(prefixo)
    assert excecoes
    for nome in excecoes:
        assert classificar_excecao_post(_exc(nome), marcador_commitado=marcador) == Classificacao(
            evento=E(evento), detalhe={"fase": "post", "error_class": nome}
        )


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bug"),
        TimeoutError(),  # prazo total (asyncio.timeout) estourado: nao listado
        RuntimeError("x"),
        httpx.DecodingError("corpo corrompido"),
        httpx.LocalProtocolError("x"),
        httpx.UnsupportedProtocol("x"),
        httpx.ProxyError("x"),
        httpx.TooManyRedirects("x"),
    ],
)
def test_excecao_nao_listada_e_dividida_pelo_marcador(exc: Exception) -> None:
    _, eventos = _linha("qualquer outra exceção")
    antes, depois = (E(e) for e in eventos)
    nome = type(exc).__name__
    assert classificar_excecao_post(exc, marcador_commitado=False) == Classificacao(
        evento=antes, detalhe={"fase": "post", "error_class": nome}
    )
    assert classificar_excecao_post(exc, marcador_commitado=True) == Classificacao(
        evento=depois, detalhe={"fase": "post", "error_class": nome}
    )
    assert MATRIZ[(S.ENVIANDO, antes)].para is S.ERRO_TECNICO
    assert MATRIZ[(S.ENVIANDO, depois)].para is S.INCERTO


@pytest.mark.parametrize("exc", [FalhaNoRefetchCsrf(None), CsrfIndisponivel(None)])
def test_sem_token_dentro_do_post_nenhum_post_processado_saiu(exc: Exception) -> None:
    assert classificar_excecao_post(exc, marcador_commitado=True) == Classificacao(
        evento=E.FALHA_ANTES_POST,
        detalhe={"fase": "post", "error_class": type(exc).__name__},
    )


def test_pool_morto_readerror_depois_da_escrita_vai_para_incerto() -> None:
    c = classificar_excecao_post(_exc("ReadError"), marcador_commitado=True)
    assert c.evento is E.CONEXAO_CAIDA_APOS_POST
    assert MATRIZ[(S.ENVIANDO, c.evento)].para is S.INCERTO


# ---- CSRF por status -------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(status_da_regra(3) & {401, 403}))
def test_csrf_401_403_e_tecnico_sem_retry(status: int) -> None:
    _, (evento,) = _linha("CSRF fetch: `401`/`403`")
    assert classificar_csrf(_r(status), token_obtido=False) == Classificacao(
        evento=E(evento), detalhe={"fase": "csrf", "status": status}
    )
    assert MATRIZ[(S.ENVIANDO, E(evento))].para is S.ERRO_TECNICO


def test_csrf_200_com_token_e_ok() -> None:
    assert classificar_csrf(_r(200), token_obtido=True) is None


@pytest.mark.parametrize("status", range(100, 600))
def test_csrf_exaustivo(status: int) -> None:
    for token in (True, False):
        c = classificar_csrf(_r(status), token_obtido=token)
        if status == 200 and token:
            assert c is None
        elif status in (401, 403):
            assert c == Classificacao(E.SAP_4XX_TECNICO, {"fase": "csrf", "status": status})
        else:
            assert c == Classificacao(E.FALHA_ANTES_POST, {"fase": "csrf", "status": status})


# ---- Resposta do POST: precedencia da §4 ------------------------------------------------------


def test_conjuntos_de_status_batem_com_o_md() -> None:
    assert status_da_regra(3) == TECNICO_4XX
    assert status_da_regra(4) == NEGOCIO_4XX
    assert eventos_da_regra(1) == ("SAP_5XX_APOS_POST",)
    assert eventos_da_regra(3) == eventos_da_regra(6) == ("SAP_4XX_TECNICO",)
    assert eventos_da_regra(4) == eventos_da_regra(5) == ("SAP_4XX_NEGOCIO",)
    assert eventos_da_regra(7)[0] == "FALHA_APOS_RESPOSTA"


def _esperado(status: int, *, com_details: bool) -> E:
    """A precedencia da §4 reescrita a partir do .md (independente do codigo)."""
    if 500 <= status <= 599:
        return E(eventos_da_regra(1)[0])
    if status in status_da_regra(3):
        return E(eventos_da_regra(3)[0])
    if status in status_da_regra(4):
        return E(eventos_da_regra(4)[0])
    if 400 <= status <= 499:
        return E(eventos_da_regra(5 if com_details else 6)[0])
    return E.FALHA_APOS_RESPOSTA  # 1xx, 2xx != 201 (201 tem teste proprio) e 3xx (regra 7)


@pytest.mark.parametrize("status", [s for s in range(100, 600) if s != 201])
def test_post_exaustivo_por_status(status: int) -> None:
    for corpo, com_details in ((_COM_DETAILS, True), (_erro(), False), (b"", False)):
        c = classificar_resposta_post(_r(status, corpo))
        assert c.evento is _esperado(status, com_details=com_details), (status, corpo)
        assert c.detalhe == {"fase": "post", "status": status}
        assert c.sap_contract_number is None


def test_403_csrf_de_novo_depois_do_reenvio_e_tecnico() -> None:
    c = classificar_resposta_post(_r(403, _COM_DETAILS, {"x-csrf-token": "Required"}))
    assert c.evento is E.SAP_4XX_TECNICO


def test_tecnico_tem_precedencia_sobre_details() -> None:
    for status in sorted(TECNICO_4XX):
        assert classificar_resposta_post(_r(status, _COM_DETAILS)).evento is E.SAP_4XX_TECNICO


def test_4xx_e_5xx_levam_as_mensagens_do_erro() -> None:
    esperadas = (
        MensagemSap(code="C", message="M", target=None, path=None),
        MensagemSap(code="D", message="detalhe", target="SoldToParty", path="SoldToParty"),
    )
    for status in (400, 418, 404, 500):
        assert classificar_resposta_post(_r(status, _COM_DETAILS)).mensagens == esperadas
    assert classificar_resposta_post(_r(302, _COM_DETAILS)).mensagens == ()
    assert classificar_resposta_post(_r(400, b"<html/>")).mensagens == ()


@pytest.mark.parametrize("status", [99, 600, 0, 999])
def test_status_fora_de_100_599_e_falha_apos_resposta(status: int) -> None:
    assert classificar_resposta_post(_r(status)) == Classificacao(
        E.FALHA_APOS_RESPOSTA, {"fase": "post", "status": status}
    )


# ---- 201 ---------------------------------------------------------------------------------------


def test_201_ok_com_numero_canonico_e_avisos() -> None:
    avisos = json.dumps([{"code": "A", "message": "aviso"}])
    c = classificar_resposta_post(
        _r(201, b'{"SalesContract":"40001234"}', {"sap-messages": avisos})
    )
    assert c == Classificacao(
        evento=E.SAP_201,
        detalhe={"fase": "post", "status": 201},
        sap_contract_number="0040001234",
        mensagens=(MensagemSap(code="A", message="aviso", target=None, path=None),),
    )


@pytest.mark.parametrize(
    ("corpo", "error_class"),
    [
        (b"", "RespostaInvalida"),
        (b"nao-json", "RespostaInvalida"),
        (b"{}", "RespostaInvalida"),
        (b'{"SalesContract":null}', "RespostaInvalida"),
        (b'{"SalesContract":40001234}', "RespostaInvalida"),
        (b'{"SalesContract":"ABC123"}', "DomainValidationError"),
        (b'{"SalesContract":"12345678901"}', "DomainValidationError"),
        (b'{"SalesContract":"0000000000"}', "DomainValidationError"),
        (b'{"SalesContract":"   "}', "DomainValidationError"),
    ],
)
def test_201_fora_do_formato_e_falha_apos_resposta(corpo: bytes, error_class: str) -> None:
    c = classificar_resposta_post(_r(201, corpo))
    assert c == Classificacao(
        evento=E.FALHA_APOS_RESPOSTA,
        detalhe={"fase": "post", "status": 201, "error_class": error_class},
    )
    assert MATRIZ[(S.ENVIANDO, c.evento)].para is S.INCERTO


# ---- Propriedade: nada depois do POST volta para a fila -----------------------------------------

_DEPOIS_DO_POST = [
    "WriteError",
    "WriteTimeout",
    "ReadTimeout",
    "ReadError",
    "RemoteProtocolError",
    "DecodingError",
    "LocalProtocolError",
]


@given(
    status=st.integers(min_value=0, max_value=999),
    corpo=st.one_of(
        st.binary(max_size=64),
        st.sampled_from([_COM_DETAILS, _erro(), b'{"SalesContract":"1"}', b"{}"]),
    ),
    headers=st.dictionaries(
        st.sampled_from(["x-csrf-token", "sap-messages", "location"]), st.text(max_size=20)
    ),
)
def test_propriedade_resposta_lida_nunca_leva_a_na_fila(
    status: int, corpo: bytes, headers: dict[str, str]
) -> None:
    c = classificar_resposta_post(_r(status, corpo, headers))
    assert MATRIZ[(S.ENVIANDO, c.evento)].para is not S.NA_FILA


@given(nome=st.sampled_from(_DEPOIS_DO_POST), marcador=st.booleans())
def test_propriedade_excecao_depois_dos_bytes_nunca_leva_a_na_fila(
    nome: str, *, marcador: bool
) -> None:
    c = classificar_excecao_post(_exc(nome), marcador_commitado=marcador)
    assert MATRIZ[(S.ENVIANDO, c.evento)].para is not S.NA_FILA
