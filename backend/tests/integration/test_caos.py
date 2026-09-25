"""Caos real (D13): o worker e derrubado (``os._exit``) em cada ponto depois do marcador.

Um processo filho pega o job, grava o marcador e morre no ponto. O "segundo
worker" (aqui) roda o recover depois do lock vencer: o contrato termina em
``INCERTO`` via ``LOCK_EXPIRADO_COM_ENVIO`` e nunca volta a ``NA_FILA``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.process_outbox_job import PontoCaos, Processado
from app.application.recover_expired_locks import recuperar_lock_expirado
from app.domain.enums import ContractStatus, TransitionEvent
from app.infrastructure.db.modelos import contract_submissions, outbox_jobs
from app.infrastructure.db.uow import fabrica_de_uow
from app.infrastructure.relogio import RelogioDoSistema
from tests.integration._sap_fake import contrato_na_fila
from tests.integration._worker_caos import MORREU_NO_PONTO
from tests.integration.conftest import RelogioFixo
from tests.unit.application.fakes import FakeMetrics

E = TransitionEvent
_BACKEND = Path(__file__).resolve().parents[2]
_PONTOS = [p for p in PontoCaos if p is not PontoCaos.NO_FALLBACK]


async def _recuperar_quando_liberar(engine: AsyncEngine, metricas: FakeMetrics) -> Processado:
    """O Postgres solta o lock da linha quando nota a conexao morta: tenta por alguns segundos."""
    futuro = RelogioFixo(datetime.now(UTC) + timedelta(hours=1))
    for _ in range(100):
        r = await recuperar_lock_expirado(
            nova_uow=fabrica_de_uow(engine, futuro), relogio=futuro, metricas=metricas
        )
        if r is not None:
            return r
        await asyncio.sleep(0.1)
    raise AssertionError("o recover nao achou o job travado pelo worker morto")


@pytest.mark.parametrize("ponto", _PONTOS, ids=lambda p: p.value)
async def test_worker_derrubado_depois_do_marcador_termina_em_incerto(
    engine: AsyncEngine, url_banco: str, ponto: PontoCaos
) -> None:
    relogio = RelogioDoSistema()
    nova_uow = fabrica_de_uow(engine, relogio)
    cid = await contrato_na_fila(nova_uow, relogio)

    filho = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.integration._worker_caos",
        ponto.value,
        cwd=_BACKEND,
        env={**os.environ, "CAOS_DATABASE_URL": url_banco},
    )
    assert await asyncio.wait_for(filho.wait(), 120) == MORREU_NO_PONTO

    metricas = FakeMetrics()
    r = await _recuperar_quando_liberar(engine, metricas)
    assert (r.evento, r.para) == (E.LOCK_EXPIRADO_COM_ENVIO, ContractStatus.INCERTO)

    async with nova_uow() as uow:
        registro = await uow.contratos.obter(cid)
        eventos = [e.transicao.evento for e in await uow.eventos.listar(cid)]
    assert registro is not None
    assert registro.status is ContractStatus.INCERTO
    assert eventos == [E.SUBMETER, E.WORKER_PEGOU, E.LOCK_EXPIRADO_COM_ENVIO]
    assert [n for n, _ in metricas.chamadas] == ["contrato_incerto"]
    async with engine.connect() as conn:
        envios = await conn.scalar(select(func.count()).select_from(contract_submissions))
        job = (await conn.execute(select(outbox_jobs))).one()
    assert envios == 1  # o marcador foi commitado antes da morte
    assert job.concluido_em is not None  # nunca reagendado
    assert job.last_error is None
    assert job.attempts == 1
