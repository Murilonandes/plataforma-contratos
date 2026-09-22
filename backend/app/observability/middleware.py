"""Middleware de correlation id (header ``X-Request-ID``)."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, clear_contextvars

HEADER = "X-Request-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Le ou gera ``X-Request-ID``, faz bind no structlog e devolve no response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = request.headers.get(HEADER) or str(uuid.uuid4())
        clear_contextvars()
        bind_contextvars(correlation_id=correlation_id)
        try:
            response = await call_next(request)
        finally:
            clear_contextvars()
        response.headers[HEADER] = correlation_id
        return response
