"""Factory da aplicacao FastAPI."""

from __future__ import annotations

from fastapi import FastAPI

from app.api import health
from app.api.deps import get_settings
from app.observability.logging import configure_logging
from app.observability.middleware import CorrelationIdMiddleware
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Monta a app. Sem ``settings``, carrega do ambiente (fail-closed)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="Plataforma de Contratos SAP", version="0.1.0")
    app.state.settings = settings
    app.state.readiness_checks = {}

    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    return app
