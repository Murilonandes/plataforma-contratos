"""Outbox com transacoes concorrentes reais (Tarefa 2.4, D4).

- Varias unidades de trabalho abertas ao mesmo tempo nunca pegam o mesmo job
  (``FOR UPDATE SKIP LOCKED``) e a segunda nao espera a primeira.
- Fencing: o worker A perde o lock por expiracao, o recover (B) reivindica com
  token novo, e a partir dai nada que A tente (confirmar, reagendar, concluir)
  grava. Quem conclui e B.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.ports import (
    EntradaParcelas,
    NovoContrato,
    NovoJob,
    SnapshotContrato,
)
from app.domain.contract import Contract
from app.infrastructure.db.modelos import outbox_jobs
from app.infrastructure.db.uow import SqlUnitOfWork
from tests.integration.conftest import RelogioFixo
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
LOCK = timedelta(seconds=600)
_CONTRATO = Contract.criar(payload_exemplo_como_entrada())


def _uow(engine: AsyncEngine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine, RelogioFixo(T0))


async def _enfileirar(engine: AsyncEngine, n: int) -> list[UUID]:
    ids = []
    async with _uow(engine) as uow:
        for _ in range(n):
            c = NovoContrato(
                id=uuid4(),
                origin="WEB",
                created_by="oid",
                idempotency_key=str(uuid4()),
                pedido_sysfertil=None,
                entrada={},
            )
            s = SnapshotContrato(
                id=uuid4(),
                contract_id=c.id,
                contrato=_CONTRATO,
                algoritmo_parcelas="maior-resto/1",
                entrada_parcelas=EntradaParcelas(
                    total=Decimal("23299.55"),
                    pesos=(1, 1, 1),
                    datas=(date(2026, 9, 4), date(2026, 10, 4), date(2026, 11, 3)),
                    form_pag="K",
                ),
            )
            job = NovoJob(
                id=uuid4(), contract_id=c.id, snapshot_id=s.id, run_after=T0, correlation_id="c"
            )
            await uow.contratos.inserir(c)
            await uow.snapshots.gravar(s)
            await uow.outbox.enfileirar(job)
            ids.append(job.id)
        await uow.commit()
    return ids


async def test_duas_unidades_abertas_pegam_jobs_diferentes_e_a_segunda_nao_espera(
    engine: AsyncEngine,
) -> None:
    await _enfileirar(engine, 2)
    async with _uow(engine) as a, _uow(engine) as b:
        pa = await a.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK)  # linha travada por A
        pb = await asyncio.wait_for(b.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK), 5)
        assert pa is not None
        assert pb is not None
        assert pa.id != pb.id
        pc = await asyncio.wait_for(b.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK), 5)
        assert pc is None  # o de A esta travado por outra transacao: pulado, sem esperar
        await a.commit()
        await b.commit()


async def _worker(engine: AsyncEngine, processados: list[UUID]) -> None:
    while True:
        async with _uow(engine) as uow:
            pego = await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK)
            if pego is None:
                return
            await asyncio.sleep(0)  # deixa os outros workers disputarem
            assert await uow.outbox.concluir(pego.id, lock_token=pego.lock_token)
            await uow.commit()
            processados.append(pego.id)


async def test_quatro_workers_concorrentes_processam_cada_job_exatamente_uma_vez(
    engine: AsyncEngine,
) -> None:
    ids = await _enfileirar(engine, 20)
    processados: list[UUID] = []
    await asyncio.gather(*(_worker(engine, processados) for _ in range(4)))
    assert sorted(processados) == sorted(ids)  # nenhum repetido, nenhum faltando
    async with engine.connect() as conn:
        pendentes = (
            await conn.execute(select(outbox_jobs.c.id).where(outbox_jobs.c.concluido_em.is_(None)))
        ).all()
    assert pendentes == []


async def test_fencing_worker_que_perdeu_o_lock_nao_grava_nada(engine: AsyncEngine) -> None:
    (job_id,) = await _enfileirar(engine, 1)
    async with _uow(engine) as uow:
        a = await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK)
        await uow.commit()
    assert a is not None

    depois = T0 + LOCK + timedelta(seconds=1)  # o lock de A expirou
    async with _uow(engine) as uow:
        assert await uow.outbox.pegar_proximo(agora=depois, lock_ate=depois + LOCK) is None
        b = await uow.outbox.reivindicar_expirado(agora=depois, lock_ate=depois + LOCK)
        await uow.commit()
    assert b is not None
    assert (b.id, b.tentativa) == (job_id, a.tentativa)
    assert b.lock_token != a.lock_token

    async with _uow(engine) as uow:  # o "commit 3" de A
        assert not await uow.outbox.confirmar_lock(job_id, lock_token=a.lock_token)
        assert not await uow.outbox.reagendar(
            job_id, lock_token=a.lock_token, run_after=depois, ultimo_erro="A"
        )
        assert not await uow.outbox.concluir(job_id, lock_token=a.lock_token)
        await uow.commit()

    async with engine.connect() as conn:
        linha = (await conn.execute(select(outbox_jobs).where(outbox_jobs.c.id == job_id))).one()
    assert (linha.lock_token, linha.concluido_em, linha.last_error) == (b.lock_token, None, None)

    async with _uow(engine) as uow:
        assert await uow.outbox.confirmar_lock(job_id, lock_token=b.lock_token)
        assert await uow.outbox.concluir(job_id, lock_token=b.lock_token)
        await uow.commit()


async def test_job_com_lock_expirado_volta_a_ser_elegivel_so_pelo_recover(
    engine: AsyncEngine,
) -> None:
    await _enfileirar(engine, 1)
    async with _uow(engine) as uow:
        assert await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK) is not None
        await uow.commit()
    muito_depois = T0 + 10 * LOCK
    async with _uow(engine) as uow:
        assert await uow.outbox.pegar_proximo(agora=muito_depois, lock_ate=muito_depois) is None
        assert await uow.outbox.reivindicar_expirado(
            agora=muito_depois, lock_ate=muito_depois + LOCK
        )
