"""Testes do ``CorrelationIdMiddleware`` (X-Request-ID + bind no logger).

Usa ``httpx.AsyncClient`` + ``ASGITransport`` (padrao de teste de API do
ARCHITECTURE.md §11), sem ``starlette.testclient``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import structlog
from fastapi import FastAPI

from app.observability.logging import configure_logging
from app.observability.middleware import CorrelationIdMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/eco")
    async def eco() -> dict[str, object]:
        ctx = structlog.contextvars.get_contextvars()
        return {"correlation_id": ctx.get("correlation_id")}

    return app


@pytest.fixture
async def cliente() -> AsyncIterator[httpx.AsyncClient]:
    configure_logging(level="INFO")
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://teste") as c:
        yield c


async def test_gera_uuid4_se_header_ausente(cliente: httpx.AsyncClient) -> None:
    resp = await cliente.get("/eco")
    assert resp.status_code == 200
    cid = resp.headers["X-Request-ID"]
    assert uuid.UUID(cid).version == 4
    assert resp.json()["correlation_id"] == cid


async def test_preserva_header_fornecido(cliente: httpx.AsyncClient) -> None:
    resp = await cliente.get("/eco", headers={"X-Request-ID": "abc-123"})
    assert resp.headers["X-Request-ID"] == "abc-123"
    assert resp.json()["correlation_id"] == "abc-123"


async def test_contextvars_limpo_apos_request(cliente: httpx.AsyncClient) -> None:
    await cliente.get("/eco", headers={"X-Request-ID": "req-1"})
    assert "correlation_id" not in structlog.contextvars.get_contextvars()


async def test_correlation_id_unico_por_request(cliente: httpx.AsyncClient) -> None:
    a = (await cliente.get("/eco")).headers["X-Request-ID"]
    b = (await cliente.get("/eco")).headers["X-Request-ID"]
    assert a != b


@pytest.mark.parametrize(
    "valido", ["abc-123", "A.b_c-9", "x" * 128, "0f8fad5b-d9cb-469f-a165-70867728950e"]
)
async def test_x_request_id_valido_e_preservado(cliente: httpx.AsyncClient, valido: str) -> None:
    resp = await cliente.get("/eco", headers={"X-Request-ID": valido})
    assert resp.headers["X-Request-ID"] == valido


@pytest.mark.parametrize(
    "invalido",
    ["x" * 129, "com espaco", "tem/barra", 'aspas"', "chave=valor", "ç-acento", "a;b", "{json}"],
)
async def test_x_request_id_invalido_e_substituido_por_uuid4(
    cliente: httpx.AsyncClient, invalido: str
) -> None:
    resp = await cliente.get("/eco", headers={"X-Request-ID": invalido.encode("utf-8")})
    cid = resp.headers["X-Request-ID"]
    assert cid != invalido
    assert uuid.UUID(cid).version == 4
    assert resp.json()["correlation_id"] == cid
