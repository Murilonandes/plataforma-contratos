"""Middleware de correlation id (header ``X-Request-ID``) e access log.

O access log do uvicorn fica desligado (ele loga a query string crua e sai
depois que o contexto ja foi limpo, sem ``correlation_id``). Aqui cada request
gera um ``http_request`` com method, path SEM query, status, duration_ms e
correlation_id.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, clear_contextvars

HEADER = "X-Request-ID"

# ID do cliente so e aceito se for curto e de charset seguro; senao geramos um
# novo (evita log inchado e IDs forjados/colididos na correlacao de auditoria).
_ID_VALIDO = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

_log = structlog.get_logger("app.http")


def _correlation_id(recebido: str | None) -> str:
    if recebido is not None and _ID_VALIDO.fullmatch(recebido):
        return recebido
    return str(uuid.uuid4())


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Le (se valido) ou gera ``X-Request-ID``, faz bind no structlog, loga o acesso."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = _correlation_id(request.headers.get(HEADER))
        clear_contextvars()
        bind_contextvars(correlation_id=correlation_id)
        inicio = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            _log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status,
                duration_ms=round((time.perf_counter() - inicio) * 1000, 2),
            )
            clear_contextvars()
        response.headers[HEADER] = correlation_id
        return response
