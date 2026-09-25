"""Entrypoint do worker do outbox (``python -m app.entrypoints.worker``).

Carrega ``WorkerSettings`` (fail-closed: sem banco ou sem config SAP valida, sai
com 1) e monta o laco (Tarefa 2.9): engine asyncpg, ``ClienteSap`` +
``GatewaySap``, ``ProcessadorOutbox``, recover e ``MetricasEmLog``. SIGTERM/SIGINT
ligam o ``parar``: a tentativa em curso termina e nenhum job novo e pego.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
from collections.abc import Callable
from datetime import timedelta
from functools import partial

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.process_outbox_job import ConfigWorker, ProcessadorOutbox
from app.application.recover_expired_locks import recuperar_lock_expirado
from app.application.worker_loop import executar_worker
from app.domain.contract import Contract
from app.infrastructure.db.uow import fabrica_de_uow
from app.infrastructure.relogio import RelogioDoSistema
from app.infrastructure.sap.client import ClienteSap, ConfigSap
from app.infrastructure.sap.gateway import GatewaySap
from app.infrastructure.sap.mapper import to_json, to_payload
from app.observability.logging import configure_logging
from app.observability.metricas import MetricasEmLog
from app.settings import WorkerSettings


def config_sap(settings: WorkerSettings) -> ConfigSap:
    return ConfigSap(
        base_url=str(settings.sap_base_url),
        sap_client=settings.sap_client,
        usuario=settings.sap_user.get_secret_value(),
        senha=settings.sap_pass.get_secret_value(),
        timeout_connect_s=settings.sap_timeout_connect_s,
        timeout_read_s=settings.sap_timeout_read_s,
        decimal_as_string=settings.sap_decimal_as_string,
    )


def _corpo(contrato: Contract, *, decimal_as_string: bool) -> bytes:
    return to_json(to_payload(contrato, decimal_as_string=decimal_as_string))


_SINAIS = (signal.SIGTERM, signal.SIGINT)


def _instalar_sinais(parar: asyncio.Event) -> Callable[[], None]:
    """Liga SIGTERM/SIGINT ao ``parar``; devolve quem desfaz (restaura os anteriores)."""
    loop = asyncio.get_running_loop()
    try:
        for sinal in _SINAIS:
            loop.add_signal_handler(sinal, parar.set)
    except NotImplementedError:  # Windows (dev): sem add_signal_handler
        anteriores = {s: signal.getsignal(s) for s in _SINAIS}
        for sinal in _SINAIS:
            signal.signal(sinal, lambda *_: loop.call_soon_threadsafe(parar.set))

        def restaurar() -> None:
            for s, handler in anteriores.items():
                signal.signal(s, handler)

        return restaurar

    def remover() -> None:
        for s in _SINAIS:
            loop.remove_signal_handler(s)

    return remover


async def rodar(settings: WorkerSettings, parar: asyncio.Event | None = None) -> None:
    parar = parar or asyncio.Event()
    desfazer_sinais = _instalar_sinais(parar)
    engine = create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    relogio = RelogioDoSistema()
    nova_uow = fabrica_de_uow(engine, relogio)
    metricas = MetricasEmLog()
    try:
        async with ClienteSap(config_sap(settings)) as cliente:
            processador = ProcessadorOutbox(
                nova_uow=nova_uow,
                relogio=relogio,
                gateway=GatewaySap(cliente),
                metricas=metricas,
                config=ConfigWorker(
                    worker_id=f"{socket.gethostname()}:{os.getpid()}",
                    lock_timeout=timedelta(seconds=settings.sap_lock_timeout_s),
                    max_tentativas=settings.sap_max_tentativas,
                ),
                corpo_de=partial(_corpo, decimal_as_string=settings.sap_decimal_as_string),
            )
            await executar_worker(
                processar=processador.processar_proximo,
                recuperar=partial(
                    recuperar_lock_expirado, nova_uow=nova_uow, relogio=relogio, metricas=metricas
                ),
                parar=parar,
                intervalo_s=settings.worker_poll_interval_s,
            )
    finally:
        await engine.dispose()
        desfazer_sinais()


def _executar(settings: WorkerSettings) -> None:
    asyncio.run(rodar(settings))


def main() -> None:
    # Bootstrap antes do Settings: o erro fail-closed tambem sai em JSON.
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    log = structlog.get_logger(__name__)
    try:
        settings = WorkerSettings()
    except ValidationError as exc:
        # include_input=False: o input e o env inteiro (inclui SAP_PASS e DATABASE_URL).
        log.critical(
            "startup_falhou_config_invalida",
            erros=exc.errors(include_input=False, include_url=False, include_context=False),
        )
        sys.exit(1)
    configure_logging(settings.log_level)
    try:
        _executar(settings)
    except Exception:
        structlog.get_logger(__name__).exception("worker_falhou")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
