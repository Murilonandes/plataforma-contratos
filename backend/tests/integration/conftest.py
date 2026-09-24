"""Infra dos testes de integracao (PostgreSQL 16 real).

- Servidor: ``TEST_DATABASE_URL`` (ex.: cluster local
  ``postgresql+asyncpg://teste@127.0.0.1:55432/postgres``) ou, sem ele, um
  container ``postgres:16-alpine`` via testcontainers (o CI usa este).
- Cada teste recebe um **banco novo**, ja migrado com ``alembic upgrade head``,
  e apagado no fim: nenhum teste ve o estado de outro.
- Todo teste daqui e marcado ``integration`` (fora do ``pytest`` padrao; o job
  ``integration`` do CI roda ``pytest -m integration``).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_BACKEND = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(str(item.fspath)).is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def url_servidor() -> Iterator[str]:
    """URL (asyncpg) do banco de manutencao ``postgres`` do servidor de teste."""
    externo = os.environ.get("TEST_DATABASE_URL")
    if externo:
        yield externo
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


def alembic_config(url: str) -> Config:
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


async def rodar_alembic(url: str, comando: str, *args: str) -> None:
    """``env.py`` usa ``asyncio.run``: roda em thread para nao colidir com o loop do teste."""
    await asyncio.to_thread(getattr(command, comando), alembic_config(url), *args)


@pytest.fixture
async def url_banco(url_servidor: str) -> AsyncIterator[str]:
    """Banco novo e migrado (``upgrade head``) por teste."""
    nome = f"t_{uuid4().hex[:12]}"
    admin = create_async_engine(url_servidor, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as c:
            await c.execute(text(f'CREATE DATABASE "{nome}"'))
        url = make_url(url_servidor).set(database=nome).render_as_string(hide_password=False)
        await rodar_alembic(url, "upgrade", "head")
        yield url
        async with admin.connect() as c:
            await c.execute(text(f'DROP DATABASE "{nome}" WITH (FORCE)'))
    finally:
        await admin.dispose()


@pytest.fixture
async def engine(url_banco: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(url_banco)
    try:
        yield eng
    finally:
        await eng.dispose()
