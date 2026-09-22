"""Middleware de correlation id (header ``X-Request-ID``)."""

from __future__ import annotations

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from structlog.contextvars import bind_contextvars, clear_contextvars

HEADER = "X-Request-ID"

# ID do cliente so e aceito se for curto e de charset seguro; senao geramos um
# novo (evita log inchado e IDs forjados/colididos na correlacao de auditoria).
_ID_VALIDO = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _correlation_id(recebido: str | None) -> str:
    if recebido is not None and _ID_VALIDO.fullmatch(recebido):
        return recebido
    return str(uuid.uuid4())


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Le (se valido) ou gera ``X-Request-ID``, faz bind no structlog e devolve no response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = _correlation_id(request.headers.get(HEADER))
        clear_contextvars()
        bind_contextvars(correlation_id=correlation_id)
        try:
            response = await call_next(request)
        finally:
            clear_contextvars()
        response.headers[HEADER] = correlation_id
        return response
