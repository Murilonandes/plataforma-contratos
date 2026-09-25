"""Processo filho do caos real (D13): processa UM job e morre com ``os._exit`` no ponto.

Uso: ``CAOS_DATABASE_URL=... python -m tests.integration._worker_caos <ponto>``.
Sai com 17 se morreu no ponto; com 3 se passou sem morrer (o teste falha).
"""

from __future__ import annotations

import asyncio
import os
import sys

import respx
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.process_outbox_job import PontoCaos
from app.infrastructure.relogio import RelogioDoSistema
from app.infrastructure.sap.client import ClienteSap
from tests.integration._sap_fake import config_sap, montar_sap, processador

MORREU_NO_PONTO = 17
NAO_MORREU = 3


async def _main(url: str, ponto: PontoCaos) -> None:
    engine = create_async_engine(url)

    async def gancho(p: PontoCaos) -> None:
        if p is ponto:
            os._exit(MORREU_NO_PONTO)  # morte seca: sem finally, sem rollback do cliente

    with respx.mock(assert_all_called=False) as router:
        montar_sap(router)
        async with ClienteSap(config_sap()) as cliente:
            p = processador(engine, RelogioDoSistema(), cliente, "filho", gancho=gancho)
            await p.processar_proximo()
    os._exit(NAO_MORREU)


if __name__ == "__main__":
    asyncio.run(_main(os.environ["CAOS_DATABASE_URL"], PontoCaos(sys.argv[1])))
