"""Consultas de saude da API no Postgres (Tarefa 2.10)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from app.infrastructure.db.heartbeat import banco_ok, ultimo_csrf_ok
from app.infrastructure.db.uow import SqlUnitOfWork
from tests.integration.conftest import RelogioFixo

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


async def test_banco_ok(engine: AsyncEngine) -> None:
    assert await banco_ok(engine) is True


async def test_ultimo_csrf_ok_le_o_que_o_worker_gravou(engine: AsyncEngine) -> None:
    assert await ultimo_csrf_ok(engine) is None
    async with SqlUnitOfWork(engine, RelogioFixo(T0)) as uow:
        await uow.heartbeat.registrar_csrf_ok(agora=T0)
        await uow.commit()
    assert await ultimo_csrf_ok(engine) == T0
