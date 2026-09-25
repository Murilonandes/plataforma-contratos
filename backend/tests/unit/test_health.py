"""Testes de ``/health/live`` e ``/health/ready`` via ``create_app``."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.api import health
from app.api.deps import get_settings
from app.main import create_app
from app.settings import ApiSettings
from tests.conftest import ConfiguraSap
from tests.unit.application.fakes import FakeClock, FakeMetrics


@pytest.fixture
def app(sap_env: ConfiguraSap) -> FastAPI:
    sap_env()
    return create_app(ApiSettings())


@pytest.fixture
async def cliente(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://teste") as c:
        yield c


async def _ok() -> bool:
    return True


async def _falha() -> bool:
    return False


async def _explode() -> bool:
    raise RuntimeError("banco fora")


async def test_live_retorna_ok(cliente: httpx.AsyncClient) -> None:
    resp = await cliente.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_live_passa_pelo_middleware_de_correlation_id(cliente: httpx.AsyncClient) -> None:
    resp = await cliente.get("/health/live", headers={"X-Request-ID": "hc-1"})
    assert resp.headers["X-Request-ID"] == "hc-1"


def test_ready_registra_so_o_banco_o_sap_fica_fora(app: FastAPI) -> None:
    assert set(app.state.readiness_checks) == {"db"}


async def test_ready_sem_checks_retorna_ok(app: FastAPI, cliente: httpx.AsyncClient) -> None:
    app.state.readiness_checks = {}
    resp = await cliente.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {}}


async def test_check_lento_conta_como_falha(
    app: FastAPI, cliente: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "CHECK_TIMEOUT_S", 0.05)

    async def lento() -> bool:
        await asyncio.sleep(5)
        return True

    app.state.readiness_checks = {"db": lento}
    resp = await asyncio.wait_for(cliente.get("/health/ready"), 2)
    assert resp.status_code == 503
    assert resp.json() == {"status": "fail", "checks": {"db": "fail"}}


async def test_ready_com_checks_ok(app: FastAPI, cliente: httpx.AsyncClient) -> None:
    app.state.readiness_checks["db"] = _ok
    resp = await cliente.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"db": "ok"}}


async def test_ready_retorna_503_quando_check_falha(
    app: FastAPI, cliente: httpx.AsyncClient
) -> None:
    app.state.readiness_checks["db"] = _ok
    app.state.readiness_checks["sap_csrf"] = _falha
    resp = await cliente.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "fail", "checks": {"db": "ok", "sap_csrf": "fail"}}


async def test_ready_trata_excecao_do_check_como_falha(
    app: FastAPI, cliente: httpx.AsyncClient
) -> None:
    app.state.readiness_checks["db"] = _explode
    resp = await cliente.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"] == {"db": "fail"}
    assert "banco fora" not in resp.text


def test_create_app_sem_argumento_le_settings_do_env(sap_env: ConfiguraSap) -> None:
    sap_env()
    get_settings.cache_clear()
    try:
        app = create_app()
        assert app.state.settings.app_env == "dev"
    finally:
        get_settings.cache_clear()


# ---- /health/sap (informativo, sempre 200) -------------------------------------------------

AGORA = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _heartbeat(app: FastAPI, ultimo: datetime | Exception | None) -> FakeMetrics:
    async def fonte() -> datetime | None:
        if isinstance(ultimo, Exception):
            raise ultimo
        return ultimo

    metricas = FakeMetrics()
    app.state.heartbeat_sap = fonte
    app.state.relogio = FakeClock(AGORA)
    app.state.metricas = metricas
    return metricas


async def test_sap_ok(app: FastAPI, cliente: httpx.AsyncClient) -> None:
    metricas = _heartbeat(app, AGORA - timedelta(minutes=10))
    resp = await cliente.get("/health/sap")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "ultimo_csrf_ok": "2026-09-25T11:50:00+00:00",
        "idade_s": 600,
    }
    assert metricas.chamadas == []


async def test_sap_no_limite_ainda_e_ok(app: FastAPI, cliente: httpx.AsyncClient) -> None:
    _heartbeat(app, AGORA - health.HEARTBEAT_MAX_IDADE)
    assert (await cliente.get("/health/sap")).json()["status"] == "ok"


async def test_sap_atrasado_alerta_e_continua_200(app: FastAPI, cliente: httpx.AsyncClient) -> None:
    metricas = _heartbeat(app, AGORA - timedelta(hours=1, seconds=1))
    resp = await cliente.get("/health/sap")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "atrasado",
        "ultimo_csrf_ok": "2026-09-25T10:59:59+00:00",
        "idade_s": 3601,
    }
    assert metricas.chamadas == [("sap_heartbeat_atrasado", {"idade_s": 3601})]


async def test_sap_sem_registro(app: FastAPI, cliente: httpx.AsyncClient) -> None:
    metricas = _heartbeat(app, None)
    resp = await cliente.get("/health/sap")
    assert resp.status_code == 200
    assert resp.json() == {"status": "sem_registro", "ultimo_csrf_ok": None, "idade_s": None}
    assert metricas.chamadas == []


async def test_sap_indisponivel_nao_vaza_e_continua_200(
    app: FastAPI, cliente: httpx.AsyncClient
) -> None:
    _heartbeat(app, ConnectionError("banco fora: segredo"))
    resp = await cliente.get("/health/sap")
    assert resp.status_code == 200
    assert resp.json() == {"status": "indisponivel", "ultimo_csrf_ok": None, "idade_s": None}
    assert "segredo" not in resp.text


def test_limite_do_heartbeat() -> None:
    assert timedelta(hours=1) == health.HEARTBEAT_MAX_IDADE
    assert health.CHECK_TIMEOUT_S == 3.0
