"""Bateria de contrato das portas contra o PostgreSQL 16 real (criterio da 2.4).

Os testes sao os de ``tests/unit/application/test_contrato_portas.py`` (que rodam
contra os fakes); aqui so a fixture ``nova_uow`` muda: ``SqlUnitOfWork`` num banco
novo e migrado por teste. Fake e Postgres cumprem o mesmo contrato.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.infrastructure.db.uow import SqlUnitOfWork
from tests.integration.conftest import RelogioFixo
from tests.unit.application.test_contrato_portas import *  # noqa: F403 — mesma bateria
from tests.unit.application.test_contrato_portas import T0, NovaUow


@pytest.fixture
def nova_uow(engine: AsyncEngine) -> NovaUow:
    relogio = RelogioFixo(T0)
    return lambda: SqlUnitOfWork(engine, relogio)
