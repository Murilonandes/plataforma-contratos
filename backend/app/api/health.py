"""Health checks: ``/health/live`` (processo vivo) e ``/health/ready`` (dependencias).

Os readiness checks ficam em ``app.state.readiness_checks`` (nome -> coroutine
que devolve ``bool``). Na Fase 0 o dict esta vazio; a Fase 2 registra ``db`` e
``sap_csrf``. Excecao em um check conta como falha e nao vaza no response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

ReadinessCheck = Callable[[], Awaitable[bool]]

router = APIRouter(prefix="/health", tags=["health"])

_log = structlog.get_logger(__name__)


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    checks: dict[str, ReadinessCheck] = request.app.state.readiness_checks
    resultados: dict[str, str] = {}
    for nome, check in checks.items():
        try:
            ok = await check()
        except Exception:
            _log.exception("readiness_check_falhou", check=nome)
            ok = False
        resultados[nome] = "ok" if ok else "fail"

    tudo_ok = all(v == "ok" for v in resultados.values())
    return JSONResponse(
        status_code=200 if tudo_ok else 503,
        content={"status": "ok" if tudo_ok else "fail", "checks": resultados},
    )
