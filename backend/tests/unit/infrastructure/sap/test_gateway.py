"""Gateway SAP (Tarefa 2.7): client + classificacao atras da porta ``SapContractGateway``.

Ponta a ponta com respx: nunca levanta excecao de transporte, prazo total por
chamada com ``asyncio.timeout`` (D4) e a classificacao da §4.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
import respx

from app.application.ports import DesfechoCsrf, DesfechoPost, MensagemSap, SapContractGateway
from app.domain.enums import TransitionEvent as E
from app.infrastructure.sap import gateway as modulo_gateway
from app.infrastructure.sap.client import ClienteSap, ConfigSap
from app.infrastructure.sap.gateway import GatewaySap

BASE = "https://s4-dev.example/sap/opu/odata4/sap/zapi/0001/"
POST_URL = BASE + "CriaContrato"
CID = UUID("11111111-2222-3333-4444-555555555555")
CORPO = b'{"SalesContractType":"ZCON","StatusBlock":"06"}'


def _config(connect: float = 5.0, read: float = 90.0) -> ConfigSap:
    return ConfigSap(
        base_url=BASE,
        sap_client="300",
        usuario="USR_TEC",
        senha="senha-de-teste",
        timeout_connect_s=connect,
        timeout_read_s=read,
        decimal_as_string=True,
    )


def _token_ok() -> httpx.Response:
    return httpx.Response(200, headers={"x-csrf-token": "csrf-ficticio"})


@pytest.fixture
async def gw() -> AsyncIterator[GatewaySap]:
    async with ClienteSap(_config()) as c:
        yield GatewaySap(c)


async def _preparar(gw: GatewaySap) -> DesfechoCsrf:
    return await gw.preparar(correlation_id="corr", contract_id=CID)


async def _criar(gw: GatewaySap) -> DesfechoPost:
    return await gw.criar_contrato(CORPO, correlation_id="corr", contract_id=CID)


def test_implementa_a_porta_e_prazos_do_d4() -> None:
    async def montar() -> None:
        async with ClienteSap(_config(connect=5.0, read=90.0)) as c:
            gw = GatewaySap(c)
            porta: SapContractGateway = gw  # mypy confere a porta
            assert porta is gw
            assert gw.prazo_csrf_s == 95.0
            assert gw.prazo_post_s == 285.0  # POST + refetch + POST: 3 x (connect + read)

    asyncio.run(montar())


# ---- preparar (CSRF) ---------------------------------------------------------------------------


@respx.mock
async def test_preparar_ok_e_token_em_cache_nao_refaz_o_get(gw: GatewaySap) -> None:
    rota = respx.get(BASE).mock(return_value=_token_ok())
    assert await _preparar(gw) == DesfechoCsrf.ok(token_novo=True)  # fetch real
    assert await _preparar(gw) == DesfechoCsrf.ok()  # cache: sem GET
    assert rota.call_count == 1


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [
        (httpx.Response(401), DesfechoCsrf(E.SAP_4XX_TECNICO, {"fase": "csrf", "status": 401})),
        (httpx.Response(403), DesfechoCsrf(E.SAP_4XX_TECNICO, {"fase": "csrf", "status": 403})),
        (httpx.Response(500), DesfechoCsrf(E.FALHA_ANTES_POST, {"fase": "csrf", "status": 500})),
        (httpx.Response(200), DesfechoCsrf(E.FALHA_ANTES_POST, {"fase": "csrf", "status": 200})),
        (
            httpx.ConnectError("x"),
            DesfechoCsrf(E.FALHA_ANTES_POST, {"fase": "csrf", "error_class": "ConnectError"}),
        ),
        (
            httpx.ReadTimeout("x"),
            DesfechoCsrf(E.FALHA_ANTES_POST, {"fase": "csrf", "error_class": "ReadTimeout"}),
        ),
    ],
)
@respx.mock
async def test_preparar_classifica_falhas(
    gw: GatewaySap, resposta: httpx.Response | Exception, esperado: DesfechoCsrf
) -> None:
    if isinstance(resposta, Exception):
        respx.get(BASE).mock(side_effect=resposta)
    else:
        respx.get(BASE).mock(return_value=resposta)
    assert await _preparar(gw) == esperado


async def _lento(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(5)
    return _token_ok()


@respx.mock
async def test_preparar_com_prazo_estourado_e_falha_antes_post() -> None:
    respx.get(BASE).mock(side_effect=_lento)
    async with ClienteSap(_config(connect=0.01, read=0.02)) as c:
        d = await GatewaySap(c).preparar(correlation_id="corr", contract_id=CID)
    assert d == DesfechoCsrf(E.FALHA_ANTES_POST, {"fase": "csrf", "error_class": "TimeoutError"})


# ---- criar_contrato ------------------------------------------------------------------------------


@respx.mock
async def test_201_vira_sap_201_com_numero_canonico_resposta_e_avisos(gw: GatewaySap) -> None:
    respx.get(BASE).mock(return_value=_token_ok())
    avisos = json.dumps([{"code": "A", "message": "aviso"}])
    respx.post(POST_URL).mock(
        return_value=httpx.Response(
            201, content=b'{"SalesContract":"40001234"}', headers={"sap-messages": avisos}
        )
    )
    await _preparar(gw)
    d = await _criar(gw)
    assert d.evento is E.SAP_201
    assert d.sap_contract_number == "0040001234"
    assert d.mensagens == (MensagemSap(code="A", message="aviso", target=None, path=None),)
    assert d.detalhe == {"fase": "post", "status": 201}
    assert d.resposta is not None
    assert d.resposta.corpo == b'{"SalesContract":"40001234"}'
    assert d.duracao_ms >= 0
    assert respx.calls.last.request.content == CORPO


@respx.mock
async def test_400_com_details_vira_negocio_com_mensagens(gw: GatewaySap) -> None:
    respx.get(BASE).mock(return_value=_token_ok())
    corpo = json.dumps(
        {
            "error": {
                "code": "C",
                "message": "M",
                "details": [{"code": "D", "message": "x", "target": "SoldToParty"}],
            }
        }
    ).encode()
    respx.post(POST_URL).mock(return_value=httpx.Response(400, content=corpo))
    d = await _criar(gw)
    assert d.evento is E.SAP_4XX_NEGOCIO
    assert d.resposta is not None
    assert d.resposta.status == 400
    assert [m.path for m in d.mensagens] == [None, "SoldToParty"]


@respx.mock
async def test_pool_morto_readerror_depois_da_escrita_vira_conexao_caida(gw: GatewaySap) -> None:
    """Obrigatorio (2.7): conexao do pool morta falha como ReadError depois da escrita."""
    respx.get(BASE).mock(return_value=_token_ok())
    rota = respx.post(POST_URL).mock(side_effect=httpx.ReadError("conexao do pool morta"))
    await _preparar(gw)
    d = await _criar(gw)
    assert d.evento is E.CONEXAO_CAIDA_APOS_POST
    assert d.resposta is None
    assert d.detalhe == {"fase": "post", "error_class": "ReadError"}
    assert rota.call_count == 1  # nunca reenvia


@respx.mock
async def test_connect_error_no_post_e_falha_antes_post(gw: GatewaySap) -> None:
    respx.get(BASE).mock(return_value=_token_ok())
    respx.post(POST_URL).mock(side_effect=httpx.ConnectError("x"))
    d = await _criar(gw)
    assert d.evento is E.FALHA_ANTES_POST


@respx.mock
async def test_403_csrf_duas_vezes_e_tecnico_com_dois_posts(gw: GatewaySap) -> None:
    respx.get(BASE).mock(return_value=_token_ok())
    rota = respx.post(POST_URL).mock(
        return_value=httpx.Response(403, headers={"x-csrf-token": "Required"})
    )
    d = await _criar(gw)
    assert d.evento is E.SAP_4XX_TECNICO
    assert rota.call_count == 2


@respx.mock
async def test_refetch_que_falha_e_falha_antes_post(gw: GatewaySap) -> None:
    respx.get(BASE).mock(side_effect=[_token_ok(), httpx.Response(500)])
    rota = respx.post(POST_URL).mock(
        return_value=httpx.Response(403, headers={"x-csrf-token": "Required"})
    )
    await _preparar(gw)
    d = await _criar(gw)
    assert (d.evento, d.detalhe) == (
        E.FALHA_ANTES_POST,
        {"fase": "post", "error_class": "FalhaNoRefetchCsrf"},
    )
    assert rota.call_count == 1


async def _post_lento(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(5)
    return httpx.Response(201, content=b'{"SalesContract":"1"}')


@respx.mock
async def test_prazo_total_estourado_no_post_vai_para_incerto() -> None:
    respx.get(BASE).mock(return_value=_token_ok())
    respx.post(POST_URL).mock(side_effect=_post_lento)
    async with ClienteSap(_config(connect=0.01, read=0.02)) as c:
        gw = GatewaySap(c)
        await _preparar(gw)
        d = await _criar(gw)
    assert (d.evento, d.detalhe) == (
        E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO,
        {"fase": "post", "error_class": "TimeoutError"},
    )
    assert d.resposta is None


@respx.mock
async def test_falha_nossa_depois_de_ler_a_resposta_e_falha_apos_resposta(
    gw: GatewaySap, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(_: object) -> None:
        raise RuntimeError("bug no parser")

    monkeypatch.setattr(modulo_gateway, "classificar_resposta_post", explode)
    respx.get(BASE).mock(return_value=_token_ok())
    respx.post(POST_URL).mock(return_value=httpx.Response(201, content=b"{}"))
    d = await _criar(gw)
    assert (d.evento, d.detalhe) == (
        E.FALHA_APOS_RESPOSTA,
        {"fase": "post", "status": 201, "error_class": "RuntimeError"},
    )
    assert d.resposta is not None
    assert d.resposta.corpo == b"{}"
