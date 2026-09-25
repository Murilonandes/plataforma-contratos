"""Health checks: ``/health/live``, ``/health/ready`` e ``/health/sap``.

- ``/health/ready``: os checks de ``app.state.readiness_checks`` (nome ->
  coroutine que devolve ``bool``). A Fase 2 registra so ``db``: a API NAO depende
  do SAP para receber trafego. Excecao ou demora acima de ``CHECK_TIMEOUT_S``
  conta como falha e nao vaza no response.
- ``/health/sap``: informativo, fora do ready, SEMPRE ``200`` (nao derruba o
  roteamento do Traefik). Idade do ultimo CSRF ok do worker, lida do banco:
  ``ok``, ``atrasado`` (acima de ``HEARTBEAT_MAX_IDADE``: alerta D15),
  ``sem_registro`` ou ``indisponivel`` (falha ao ler). Fila parada tambem
  envelhece o heartbeat: o worker so busca token quando tem job.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.application.ports import Clock, Metrics

ReadinessCheck = Callable[[], Awaitable[bool]]
FonteHeartbeat = Callable[[], Awaitable[datetime | None]]

CHECK_TIMEOUT_S: Final = 3.0
HEARTBEAT_MAX_IDADE: Final = timedelta(hours=1)

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
            async with asyncio.timeout(CHECK_TIMEOUT_S):
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


@dataclass(frozen=True, slots=True)
class SaudeSap:
    status: Literal["ok", "atrasado", "sem_registro"]
    idade_s: int | None


def avaliar_heartbeat(ultimo: datetime | None, agora: datetime) -> SaudeSap:
    if ultimo is None:
        return SaudeSap("sem_registro", None)
    idade = agora - ultimo
    return SaudeSap("atrasado" if idade > HEARTBEAT_MAX_IDADE else "ok", int(idade.total_seconds()))


@router.get("/sap")
async def sap(request: Request) -> JSONResponse:
    fonte: FonteHeartbeat = request.app.state.heartbeat_sap
    relogio: Clock = request.app.state.relogio
    metricas: Metrics = request.app.state.metricas
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S):
            ultimo = await fonte()
    except Exception as exc:
        _log.error("health_sap_falhou", error_class=type(exc).__name__)
        return JSONResponse(
            status_code=200,
            content={"status": "indisponivel", "ultimo_csrf_ok": None, "idade_s": None},
        )
    saude = avaliar_heartbeat(ultimo, relogio.agora())
    if saude.status == "atrasado":
        metricas.incrementar("sap_heartbeat_atrasado", idade_s=saude.idade_s or 0)
    return JSONResponse(
        status_code=200,
        content={
            "status": saude.status,
            "ultimo_csrf_ok": None if ultimo is None else ultimo.isoformat(),
            "idade_s": saude.idade_s,
        },
    )
