"""Factory da aplicacao FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import health
from app.api.deps import get_settings
from app.infrastructure.db.heartbeat import banco_ok, ultimo_csrf_ok
from app.infrastructure.relogio import RelogioDoSistema
from app.observability.logging import configure_logging
from app.observability.metricas import MetricasEmLog
from app.observability.middleware import CorrelationIdMiddleware
from app.settings import ApiSettings


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Monta a app. Sem ``settings``, carrega do ambiente (fail-closed)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    # Engine preguicoso: nada conecta ate o primeiro uso (o ready ou uma rota).
    engine = create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="Plataforma de Contratos SAP", version="0.1.0", lifespan=ciclo_de_vida)
    app.state.settings = settings
    app.state.readiness_checks = {"db": partial(banco_ok, engine)}  # o SAP NAO entra no ready
    app.state.heartbeat_sap = partial(ultimo_csrf_ok, engine)
    app.state.relogio = RelogioDoSistema()
    app.state.metricas = MetricasEmLog()

    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    return app
