"""Client HTTP do SAP (Tarefa 2.5): sessao, CSRF, POST, 403 CSRF e log allowlist.

O client e CRU: devolve a resposta (``RespostaSap``) ou deixa a excecao do httpx
subir. Quem classifica e a Tarefa 2.7. Excecao propria so uma:
``FalhaNoRefetchCsrf`` (o refetch do token depois de um 403 CSRF falhou).
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
import respx

from app.infrastructure.sap.client import ClienteSap, ConfigSap, FalhaNoRefetchCsrf
from app.observability.logging import configure_logging

BASE = "https://s4-dev.example/sap/opu/odata4/sap/zapi/0001/"
POST_URL = BASE + "CriaContrato"
CID = UUID("11111111-2222-3333-4444-555555555555")
CORPO = b'{"SalesContractType":"ZCON","StatusBlock":"06"}'
SENHA = "senha-tecnica-nao-vaza-7f3"
TOKEN = "tok-csrf-abc123"


def _config(*, decimal_as_string: bool = True) -> ConfigSap:
    return ConfigSap(
        base_url=BASE,
        sap_client="300",
        usuario="USR_TEC",
        senha=SENHA,
        timeout_connect_s=5.0,
        timeout_read_s=90.0,
        decimal_as_string=decimal_as_string,
    )


@pytest.fixture
async def cliente() -> AsyncIterator[ClienteSap]:
    async with ClienteSap(_config()) as c:
        yield c


def _token_ok(token: str = TOKEN) -> httpx.Response:
    return httpx.Response(
        200, headers={"x-csrf-token": token, "set-cookie": "SAP_SESSIONID_S4D_300=sess1; path=/"}
    )


async def _preparar(c: ClienteSap) -> None:
    await c.buscar_token(correlation_id="corr-1", contract_id=CID)


# ---- Sessao ------------------------------------------------------------------------------


def test_timeouts_e_redirect_desligado() -> None:
    c = ClienteSap(_config())
    assert c.timeout.connect == 5.0
    assert c.timeout.read == 90.0
    assert c.segue_redirect is False


@respx.mock
async def test_fetch_do_csrf_com_basic_auth_parametros_e_cache(cliente: ClienteSap) -> None:
    rota = respx.get(BASE).mock(return_value=_token_ok())
    resp = await cliente.buscar_token(correlation_id="corr-1", contract_id=CID)
    assert resp.status == 200
    assert cliente.token_em_cache == TOKEN
    req = rota.calls.last.request
    assert req.headers["x-csrf-token"] == "Fetch"
    esperado = base64.b64encode(f"USR_TEC:{SENHA}".encode()).decode()
    assert req.headers["authorization"] == f"Basic {esperado}"
    assert dict(req.url.params) == {"sap-client": "300", "saml2": "disabled"}


@respx.mock
async def test_fetch_sem_token_no_header_nao_cacheia(cliente: ClienteSap) -> None:
    respx.get(BASE).mock(return_value=httpx.Response(200))
    resp = await cliente.buscar_token(correlation_id="c", contract_id=CID)
    assert resp.status == 200
    assert cliente.token_em_cache is None


@pytest.mark.parametrize("status", [401, 403, 500])
@respx.mock
async def test_fetch_com_status_de_erro_devolve_a_resposta_e_nao_cacheia(
    cliente: ClienteSap, status: int
) -> None:
    respx.get(BASE).mock(return_value=httpx.Response(status, headers={"x-csrf-token": "x"}))
    resp = await cliente.buscar_token(correlation_id="c", contract_id=CID)
    assert resp.status == status
    assert cliente.token_em_cache is None


@respx.mock
async def test_excecao_de_transporte_no_fetch_sobe_sem_reinterpretar(cliente: ClienteSap) -> None:
    respx.get(BASE).mock(side_effect=httpx.ReadTimeout("lento"))
    with pytest.raises(httpx.ReadTimeout):
        await cliente.buscar_token(correlation_id="c", contract_id=CID)


# ---- POST --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decimal_as_string", "content_type"),
    [
        (True, "application/json;IEEE754Compatible=true"),
        (False, "application/json"),
    ],
)
@respx.mock
async def test_post_envia_os_bytes_exatos_com_token_e_cookies_da_sessao(
    decimal_as_string: bool,
    content_type: str,
) -> None:
    respx.get(BASE).mock(return_value=_token_ok())
    rota = respx.post(POST_URL).mock(
        return_value=httpx.Response(201, content=b'{"SalesContract":"40001234"}')
    )
    async with ClienteSap(_config(decimal_as_string=decimal_as_string)) as c:
        await _preparar(c)
        resp = await c.post_criar_contrato(CORPO, correlation_id="corr-1", contract_id=CID)
    assert (resp.status, resp.corpo) == (201, b'{"SalesContract":"40001234"}')
    req = rota.calls.last.request
    assert req.content == CORPO
    assert req.headers["content-type"] == content_type
    assert req.headers["accept"] == "application/json"
    assert req.headers["x-csrf-token"] == TOKEN
    assert "SAP_SESSIONID_S4D_300=sess1" in req.headers["cookie"]
    assert dict(req.url.params) == {"sap-client": "300", "saml2": "disabled"}


@respx.mock
async def test_post_sem_token_em_cache_busca_antes(cliente: ClienteSap) -> None:
    get = respx.get(BASE).mock(return_value=_token_ok())
    respx.post(POST_URL).mock(return_value=httpx.Response(201))
    await cliente.post_criar_contrato(CORPO, correlation_id="c", contract_id=CID)
    assert get.call_count == 1


@respx.mock
async def test_403_csrf_required_refaz_o_fetch_e_reenvia_uma_vez(cliente: ClienteSap) -> None:
    get = respx.get(BASE).mock(side_effect=[_token_ok("velho"), _token_ok("novo")])
    post = respx.post(POST_URL).mock(
        side_effect=[
            httpx.Response(403, headers={"x-csrf-token": "Required"}),
            httpx.Response(201, content=b"{}"),
        ]
    )
    await _preparar(cliente)
    resp = await cliente.post_criar_contrato(CORPO, correlation_id="c", contract_id=CID)
    assert resp.status == 201
    assert get.call_count == 2
    assert [c.request.headers["x-csrf-token"] for c in post.calls] == ["velho", "novo"]
    assert [c.request.content for c in post.calls] == [CORPO, CORPO]


@respx.mock
async def test_403_csrf_duas_vezes_devolve_a_segunda_resposta_sem_terceiro_post(
    cliente: ClienteSap,
) -> None:
    respx.get(BASE).mock(side_effect=[_token_ok("a"), _token_ok("b")])
    post = respx.post(POST_URL).mock(
        return_value=httpx.Response(403, headers={"X-CSRF-Token": "required"})
    )
    await _preparar(cliente)
    resp = await cliente.post_criar_contrato(CORPO, correlation_id="c", contract_id=CID)
    assert resp.status == 403
    assert resp.headers["x-csrf-token"].lower() == "required"
    assert post.call_count == 2


@respx.mock
async def test_403_sem_header_csrf_nao_refaz(cliente: ClienteSap) -> None:
    get = respx.get(BASE).mock(return_value=_token_ok())
    post = respx.post(POST_URL).mock(return_value=httpx.Response(403))
    await _preparar(cliente)
    resp = await cliente.post_criar_contrato(CORPO, correlation_id="c", contract_id=CID)
    assert resp.status == 403
    assert (get.call_count, post.call_count) == (1, 1)


@pytest.mark.parametrize(
    "refetch", [httpx.ConnectError("caiu"), httpx.Response(401), httpx.Response(200)]
)
@respx.mock
async def test_refetch_que_falha_depois_do_403_levanta_falha_no_refetch(
    cliente: ClienteSap, refetch: object
) -> None:
    respx.get(BASE).mock(side_effect=[_token_ok(), refetch])
    post = respx.post(POST_URL).mock(
        return_value=httpx.Response(403, headers={"x-csrf-token": "Required"})
    )
    await _preparar(cliente)
    with pytest.raises(FalhaNoRefetchCsrf) as exc:
        await cliente.post_criar_contrato(CORPO, correlation_id="c", contract_id=CID)
    assert post.call_count == 1  # sem token novo, nao reenvia
    assert str(exc.value) == "refetch do token CSRF falhou depois de 403 CSRF"


@respx.mock
async def test_redirect_nao_e_seguido(cliente: ClienteSap) -> None:
    respx.get(BASE).mock(return_value=_token_ok())
    post = respx.post(POST_URL).mock(
        return_value=httpx.Response(302, headers={"location": "https://login.example/"})
    )
    alvo = respx.get("https://login.example/").mock(return_value=httpx.Response(200))
    await _preparar(cliente)
    resp = await cliente.post_criar_contrato(CORPO, correlation_id="c", contract_id=CID)
    assert resp.status == 302
    assert post.call_count == 1
    assert alvo.call_count == 0


@pytest.mark.parametrize(
    "erro",
    [
        httpx.ConnectError("x"),
        httpx.ConnectTimeout("x"),
        httpx.PoolTimeout("x"),
        httpx.WriteError("x"),
        httpx.ReadTimeout("x"),
        httpx.ReadError("x"),
        httpx.RemoteProtocolError("x"),
    ],
)
@respx.mock
async def test_excecao_no_post_sobe_sem_reinterpretar(
    cliente: ClienteSap, erro: httpx.HTTPError
) -> None:
    respx.get(BASE).mock(return_value=_token_ok())
    respx.post(POST_URL).mock(side_effect=erro)
    await _preparar(cliente)
    with pytest.raises(type(erro)):
        await cliente.post_criar_contrato(CORPO, correlation_id="c", contract_id=CID)


# ---- Log allowlist -------------------------------------------------------------------------

_CHAVES_PERMITIDAS = {
    "event",
    "logger",
    "level",
    "timestamp",
    "method",
    "url",
    "status",
    "duration_ms",
    "correlation_id",
    "contract_id",
}


def _linhas(capsys: pytest.CaptureFixture[str]) -> tuple[list[dict[str, object]], str]:
    saida = capsys.readouterr()
    assert saida.err == ""
    return [json.loads(x) for x in saida.out.splitlines() if x.strip()], saida.out


@respx.mock
async def test_log_so_tem_a_allowlist_mesmo_em_debug(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG")
    respx.get(BASE).mock(return_value=_token_ok())
    respx.post(POST_URL).mock(
        side_effect=[
            httpx.Response(403, headers={"x-csrf-token": "Required"}),
            httpx.Response(201, content=b'{"SalesContract":"40001234","segredo":"corpo"}'),
        ]
    )
    async with ClienteSap(_config()) as c:
        await c.buscar_token(correlation_id="corr-9", contract_id=CID)
        await c.post_criar_contrato(CORPO, correlation_id="corr-9", contract_id=CID)
    linhas, bruto = _linhas(capsys)
    assert [(ln["method"], ln["status"]) for ln in linhas] == [
        ("GET", 200),
        ("POST", 403),
        ("GET", 200),
        ("POST", 201),
    ]
    for ln in linhas:
        assert set(ln) == _CHAVES_PERMITIDAS
        assert ln["event"] == "sap_http"
        assert "?" not in str(ln["url"])
        assert ln["correlation_id"] == "corr-9"
        assert ln["contract_id"] == str(CID)
        assert isinstance(ln["duration_ms"], int)
    assert linhas[1]["url"] == POST_URL
    for proibido in (
        SENHA,
        TOKEN,
        "Basic ",
        "sap-client",
        "saml2",
        "SAP_SESSIONID",
        "segredo",
        "ZCON",
    ):
        assert proibido not in bruto, proibido


@respx.mock
async def test_log_de_chamada_com_excecao_tem_status_nulo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("DEBUG")
    respx.get(BASE).mock(side_effect=httpx.ConnectError("sem rota"))
    async with ClienteSap(_config()) as c:
        with pytest.raises(httpx.ConnectError):
            await _preparar(c)
    linhas, _ = _linhas(capsys)
    assert len(linhas) == 1
    assert set(linhas[0]) == _CHAVES_PERMITIDAS
    assert linhas[0]["status"] is None


def test_loggers_do_httpx_e_httpcore_ficam_em_warning() -> None:
    ClienteSap(_config())
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
