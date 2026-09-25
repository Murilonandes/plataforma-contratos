"""Laco do worker do outbox (Tarefa 2.9).

A cada volta: primeiro o recover drena os locks vencidos, depois UMA tentativa de
envio. Fila vazia (ou falha da volta) espera ``intervalo_s``, interrompivel pelo
``parar``. Parada limpa (SIGTERM/SIGINT ligam o ``parar``): a tentativa em curso
termina; nenhum job novo e pego depois do sinal. Excecao numa volta e logada
(so a classe) e o laco segue: o banco pode voltar, e o lock/recover protege o
que ficou pela metade.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import structlog

from app.application.process_outbox_job import Processado

_log = structlog.get_logger("app.worker")

Passo = Callable[[], Awaitable[Processado | None]]


async def _esperar(parar: asyncio.Event, intervalo_s: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(parar.wait(), timeout=intervalo_s)


async def executar_worker(
    *,
    processar: Passo,
    recuperar: Passo,
    parar: asyncio.Event,
    intervalo_s: float,
) -> None:
    _log.info("worker_iniciado")
    while not parar.is_set():
        feito: Processado | None = None
        try:
            while not parar.is_set() and await recuperar() is not None:
                pass
            if not parar.is_set():
                feito = await processar()
        except Exception as exc:
            _log.error("volta_do_worker_falhou", error_class=type(exc).__name__)
        if feito is None:
            await _esperar(parar, intervalo_s)
    _log.info("worker_parado")
