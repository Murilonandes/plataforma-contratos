"""Worker contra PostgreSQL 16 real e SAP fake (respx). Tarefa 2.9."""

from __future__ import annotations

import asyncio
from uuid import UUID

import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.enums import ContractStatus, TransitionEvent
from app.entrypoints import worker
from app.infrastructure.db.modelos import contract_submissions, contracts, outbox_jobs
from app.infrastructure.db.uow import fabrica_de_uow
from app.infrastructure.relogio import RelogioDoSistema
from app.infrastructure.sap.client import ClienteSap
from app.settings import WorkerSettings
from tests.conftest import ConfiguraSap
from tests.integration._sap_fake import (
    BASE,
    config_sap,
    contrato_na_fila,
    montar_sap,
    processador,
)

E = TransitionEvent


async def _estado(engine: AsyncEngine) -> dict[UUID, tuple[str, str | None, int]]:
    """contract_id -> (status, numero SAP, quantidade de submissoes)."""
    envios = (
        select(contract_submissions.c.contract_id, func.count().label("n"))
        .group_by(contract_submissions.c.contract_id)
        .subquery()
    )
    async with engine.connect() as conn:
        linhas = (
            await conn.execute(
                select(
                    contracts.c.id,
                    contracts.c.status,
                    contracts.c.sap_contract_number,
                    func.coalesce(envios.c.n, 0),
                ).outerjoin(envios, envios.c.contract_id == contracts.c.id)
            )
        ).all()
    return {id_: (status, numero, n) for id_, status, numero, n in linhas}


async def test_dois_workers_processam_cada_job_exatamente_uma_vez(engine: AsyncEngine) -> None:
    relogio = RelogioDoSistema()
    nova_uow = fabrica_de_uow(engine, relogio)
    ids = [await contrato_na_fila(nova_uow, relogio) for _ in range(8)]

    with respx.mock(assert_all_called=False) as router:
        post = montar_sap(router)
        async with ClienteSap(config_sap()) as c1, ClienteSap(config_sap()) as c2:
            workers = [
                processador(engine, relogio, c1, "w1"),
                processador(engine, relogio, c2, "w2"),
            ]

            async def drenar(p: object) -> int:
                n = 0
                while await p.processar_proximo() is not None:  # type: ignore[attr-defined]
                    n += 1
                return n

            feitos = await asyncio.gather(*(drenar(w) for w in workers))

    assert sum(feitos) == 8
    assert post.call_count == 8
    estado = await _estado(engine)
    assert set(estado) == set(ids)
    assert {s for s, _, _ in estado.values()} == {ContractStatus.CRIADO.value}
    assert len({numero for _, numero, _ in estado.values()}) == 8  # um numero por contrato
    assert {n for _, _, n in estado.values()} == {1}  # um POST por contrato
    async with nova_uow() as uow:
        for cid in ids:
            eventos = [e.transicao.evento for e in await uow.eventos.listar(cid)]
            assert eventos == [E.SUBMETER, E.WORKER_PEGOU, E.SAP_201]
    async with engine.connect() as conn:
        pendentes = await conn.scalar(
            select(func.count())
            .select_from(outbox_jobs)
            .where(outbox_jobs.c.concluido_em.is_(None))
        )
    assert pendentes == 0


async def test_rodar_do_entrypoint_processa_a_fila_e_para_no_sinal(
    engine: AsyncEngine, url_banco: str, sap_env: ConfiguraSap
) -> None:
    sap_env(base_url=BASE, database_url=url_banco)
    settings = WorkerSettings()
    relogio = RelogioDoSistema()
    ids = [await contrato_na_fila(fabrica_de_uow(engine, relogio), relogio) for _ in range(3)]

    parar = asyncio.Event()
    with respx.mock(assert_all_called=False) as router:
        post = montar_sap(router)
        tarefa = asyncio.create_task(worker.rodar(settings, parar))
        for _ in range(200):
            estado = await _estado(engine)
            if all(estado[c][0] == ContractStatus.CRIADO.value for c in ids):
                break
            await asyncio.sleep(0.05)
        parar.set()
        await asyncio.wait_for(tarefa, 10)

    assert post.call_count == 3
    assert {(await _estado(engine))[c][0] for c in ids} == {ContractStatus.CRIADO.value}
