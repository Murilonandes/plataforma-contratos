"""Unit of Work SQL (``AsyncConnection`` do SQLAlchemy + asyncpg).

Uma conexao e uma transacao por unidade. ``commit`` publica e abre a transacao
seguinte (a mesma unidade pode commitar mais de uma vez, como o fake). Sair sem
``commit`` ou por excecao faz rollback. O ``Clock`` injetado alimenta os
instantes que as portas nao recebem por parametro (``created_at``,
``updated_at``, ``concluido_em``), nunca o ``now()`` do banco (D5).
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from app.application.ports import Clock
from app.infrastructure.db.repos import (
    RepoContratos,
    RepoEnvios,
    RepoEventos,
    RepoHeartbeat,
    RepoOutbox,
    RepoSnapshots,
)


class SqlUnitOfWork:
    def __init__(self, engine: AsyncEngine, relogio: Clock) -> None:
        self._engine = engine
        self._relogio = relogio
        self._conn: AsyncConnection | None = None
        self._trans: AsyncTransaction | None = None

    def _c(self) -> AsyncConnection:
        if self._conn is None:
            raise RuntimeError("SqlUnitOfWork usada fora do 'async with'")
        return self._conn

    @property
    def contratos(self) -> RepoContratos:
        return RepoContratos(self._c(), self._relogio)

    @property
    def snapshots(self) -> RepoSnapshots:
        return RepoSnapshots(self._c(), self._relogio)

    @property
    def eventos(self) -> RepoEventos:
        return RepoEventos(self._c())

    @property
    def outbox(self) -> RepoOutbox:
        return RepoOutbox(self._c(), self._relogio)

    @property
    def envios(self) -> RepoEnvios:
        return RepoEnvios(self._c())

    @property
    def heartbeat(self) -> RepoHeartbeat:
        return RepoHeartbeat(self._c())

    async def __aenter__(self) -> Self:
        self._conn = await self._engine.connect()
        self._trans = await self._conn.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._trans is not None and self._trans.is_active:
                await self._trans.rollback()
        finally:
            if self._conn is not None:
                await self._conn.close()
            self._conn = self._trans = None

    async def commit(self) -> None:
        if self._trans is None:
            raise RuntimeError("SqlUnitOfWork.commit fora do 'async with'")
        await self._trans.commit()
        self._trans = await self._c().begin()


def fabrica_de_uow(engine: AsyncEngine, relogio: Clock) -> Callable[[], SqlUnitOfWork]:
    """Uma unidade nova por chamada (cada tentativa do worker abre as suas)."""
    return lambda: SqlUnitOfWork(engine, relogio)
