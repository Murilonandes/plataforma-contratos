"""Testes de ``/health/live`` e ``/health/ready`` via ``create_app``."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import get_settings
from app.main import create_app
from app.settings import ApiSettings
from tests.conftest import ConfiguraSap


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


async def test_ready_sem_checks_retorna_ok(cliente: httpx.AsyncClient) -> None:
    resp = await cliente.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {}}


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
