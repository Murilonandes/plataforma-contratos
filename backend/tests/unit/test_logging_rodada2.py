"""Segunda revisao de seguranca da Fase 0: logging.

- bytes/bytearray (headers crus do httpx) passam pelo redator;
- o renderer nunca imprime o evento cru: falha de serializacao/formatacao vira
  uma linha JSON ``log_render_error`` sem payload;
- regex de string: qualquer valor apos "authorization", MYSAPSSO2=,
  SAP_SESSIONID_*=, userinfo em URL e query de senha/token;
- chaves: senha, pwd, auth, private_key, signature, mysapsso2, sap_sessionid*;
- sys.unraisablehook em JSON;
- access log do nosso middleware (sem query) no lugar do uvicorn.
"""

from __future__ import annotations

import gc
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI

from app.entrypoints import api
from app.observability.logging import REDACTED, configure_logging
from app.observability.middleware import CorrelationIdMiddleware

_S = "S3CR3T-9x"


def _saida(capsys: pytest.CaptureFixture[str]) -> tuple[list[dict[str, Any]], str, str]:
    cap = capsys.readouterr()
    linhas = [json.loads(x) for x in cap.out.splitlines() if x.strip()]
    return linhas, cap.out, cap.err


def _log(**campos: Any) -> None:
    structlog.get_logger("t").info("evt", **campos)


# ---- bytes -------------------------------------------------------------------


def test_headers_raw_do_httpx_em_lista_sao_redigidos(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    r = httpx.Request("POST", "https://s4/x", headers={"Authorization": f"Basic {_S}"})
    _log(headers=r.headers.raw)
    _, out, err = _saida(capsys)
    assert _S not in out + err
    assert err == ""


def test_dict_de_headers_raw_com_chaves_bytes_e_redigido_e_em_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    r = httpx.Request(
        "POST", "https://s4/x", headers={"Authorization": f"Basic {_S}", "Cookie": f"x={_S}"}
    )
    _log(headers=dict(r.headers.raw))
    linhas, out, err = _saida(capsys)
    assert err == ""
    assert _S not in out
    assert linhas[-1]["headers"]["Authorization"] == REDACTED


def test_bytearray_e_bytes_soltos_passam_pelo_regex(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    _log(corpo=bytearray(f"Authorization: Bearer {_S}".encode()), outro=b"\xff\xfe invalido")
    linhas, out, err = _saida(capsys)
    assert _S not in out
    assert err == ""
    assert "invalido" in linhas[-1]["outro"]


# ---- renderer nunca imprime o evento cru ------------------------------------


class _ReprExplode:
    def __repr__(self) -> str:
        raise RuntimeError(f"repr quebrou {_S}")


def test_objeto_que_quebra_na_serializacao_vira_log_render_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    structlog.get_logger("app.x").info("evt", segredo_no_payload=_S, obj=_ReprExplode())
    linhas, out, err = _saida(capsys)
    assert err == ""
    assert _S not in out
    assert linhas[-1]["event"] == "log_render_error"
    assert linhas[-1]["logger"] == "app.x"


def test_erro_de_formatacao_do_stdlib_nao_imprime_mensagem_crua(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    logging.getLogger("lib.x").info("hdrs %s %s", {"authorization": _S})  # falta 1 argumento
    linhas, out, err = _saida(capsys)
    assert err == ""
    assert _S not in out
    assert linhas[-1] == {"event": "log_render_error", "logger": "lib.x"}


# ---- regex de string ---------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        f"Authorization:Basic{_S}",
        f"Authorization: Basic !{_S}",
        f'Authorization: Digest username="u", response="{_S}"',
        f"proxy-authorization={_S}",
        f"Negotiate {_S}",
        f"NTLM {_S}",
        f"Cookie: SAP_SESSIONID_S4D_300={_S}; MYSAPSSO2={_S}",
        f"https://svc:{_S}@s4/x",
        f"https://{_S}@s4/x",
        f"https://s4/x?sap-password={_S}&a=1",
        f"https://s4/x?password={_S}",
        f"https://s4/x?a=1&access_token={_S}",
        f"https://s4/x?token={_S}",
    ],
)
def test_credencial_em_texto_livre_e_mascarada(
    capsys: pytest.CaptureFixture[str], texto: str
) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info(texto)
    _, out, _ = _saida(capsys)
    assert _S not in out


def test_stdlib_com_dict_formatado_por_percent_s_e_mascarado(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    logging.getLogger("foreign").info("hdrs %s", {"authorization": _S})
    _, out, _ = _saida(capsys)
    assert _S not in out


def test_token_em_mensagem_de_traceback_e_mascarado(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    try:
        raise ValueError(f"falhou em https://s4/x?token={_S}")
    except ValueError:
        structlog.get_logger("t").exception("erro")
    _, out, _ = _saida(capsys)
    assert _S not in out


def test_texto_comum_nao_e_mascarado(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info("Authorization header ausente; contrato 123 enviado")
    linhas, _, _ = _saida(capsys)
    assert linhas[-1]["event"] == "Authorization header ausente; contrato 123 enviado"


# ---- chaves ------------------------------------------------------------------


@pytest.mark.parametrize(
    "chave",
    [
        "senha",
        "pwd",
        "auth",
        "x-auth",
        "private_key",
        "signature",
        "MYSAPSSO2",
        "SAP_SESSIONID_S4D",
    ],
)
def test_chaves_novas_sao_redigidas(capsys: pytest.CaptureFixture[str], chave: str) -> None:
    configure_logging("INFO")
    _log(**{chave: _S})
    linhas, out, _ = _saida(capsys)
    assert linhas[-1][chave] == REDACTED
    assert _S not in out


@pytest.mark.parametrize("chave", ["author", "autor", "status", "path", "contract_id"])
def test_chaves_parecidas_nao_sao_redigidas(capsys: pytest.CaptureFixture[str], chave: str) -> None:
    configure_logging("INFO")
    _log(**{chave: "visivel"})
    linhas, _, _ = _saida(capsys)
    assert linhas[-1][chave] == "visivel"


# ---- unraisablehook ----------------------------------------------------------


def test_unraisablehook_emite_json_mascarado(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")

    class Y:
        def __del__(self) -> None:
            raise ValueError(f"Bearer {_S}")

    Y()
    gc.collect()
    linhas, out, err = _saida(capsys)
    assert err == ""
    assert _S not in out
    assert linhas[-1]["event"] == "excecao_nao_levantavel"
    assert "ValueError" in linhas[-1]["exception"]


# ---- access log --------------------------------------------------------------


@pytest.fixture
async def cliente() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/eco")
    async def eco() -> dict[str, str]:
        return {"ok": "sim"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://teste") as c:
        yield c


async def test_access_log_sem_query_com_correlation_id(
    cliente: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    # configure_logging no corpo do teste: o handler guarda o sys.stdout da hora,
    # e so aqui ele ja e o do capsys
    configure_logging("INFO")
    resp = await cliente.get(
        f"/eco?access_token={_S}&sap-password={_S}", headers={"X-Request-ID": "req-42"}
    )
    linhas, out, _ = _saida(capsys)
    acesso = [x for x in linhas if x["event"] == "http_request"]
    assert len(acesso) == 1
    a = acesso[0]
    assert (a["method"], a["path"], a["status"], a["correlation_id"]) == (
        "GET",
        "/eco",
        200,
        "req-42",
    )
    assert isinstance(a["duration_ms"], (int, float))
    assert a["duration_ms"] >= 0
    assert "?" not in a["path"]
    assert _S not in out
    assert resp.status_code == 200


def test_api_desliga_access_log_do_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    capturado: dict[str, object] = {}

    def uvicorn_run_falso(*_a: object, **kwargs: object) -> None:
        capturado.update(kwargs)

    monkeypatch.setattr(api.uvicorn, "run", uvicorn_run_falso)
    api.main()
    assert capturado["access_log"] is False
