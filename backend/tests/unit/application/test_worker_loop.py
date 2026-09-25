"""Laco do worker (Tarefa 2.9): recover antes, parada limpa, fila vazia espera."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from structlog.testing import capture_logs

from app.application.ports import JobPego
from app.application.process_outbox_job import Processado
from app.application.worker_loop import executar_worker
from app.domain.enums import ContractStatus, TransitionEvent
from tests.unit.application._cenario import T0

_JOB = JobPego(
    id=uuid4(),
    contract_id=uuid4(),
    snapshot_id=uuid4(),
    tentativa=1,
    lock_token=uuid4(),
    locked_until=T0,
    correlation_id="c",
)
_FEITO = Processado(_JOB, TransitionEvent.SAP_201, ContractStatus.CRIADO)


async def test_recover_drena_antes_de_cada_tentativa_e_para_quando_pedido() -> None:
    parar = asyncio.Event()
    chamadas: list[str] = []
    recuperacoes = [_FEITO, _FEITO, None, None]

    async def recuperar() -> Processado | None:
        chamadas.append("recover")
        return recuperacoes.pop(0)

    async def processar() -> Processado | None:
        chamadas.append("processar")
        if chamadas.count("processar") == 2:
            parar.set()  # sinal chega DURANTE a tentativa: ela termina e o laco sai
        return _FEITO

    with capture_logs() as logs:
        await asyncio.wait_for(
            executar_worker(processar=processar, recuperar=recuperar, parar=parar, intervalo_s=60),
            5,
        )
    assert chamadas == ["recover", "recover", "recover", "processar", "recover", "processar"]
    assert [x["event"] for x in logs] == ["worker_iniciado", "worker_parado"]


async def test_fila_vazia_espera_o_intervalo_e_o_sinal_interrompe_a_espera() -> None:
    parar = asyncio.Event()
    voltas = 0

    async def nada() -> Processado | None:
        return None

    async def processar() -> Processado | None:
        nonlocal voltas
        voltas += 1
        return None

    tarefa = asyncio.create_task(
        executar_worker(processar=processar, recuperar=nada, parar=parar, intervalo_s=0.05)
    )
    await asyncio.sleep(0.2)
    assert 2 <= voltas <= 6  # esperou entre as voltas (sem busy loop)
    parar.set()
    await asyncio.wait_for(tarefa, 1)  # a espera longa nao segura a parada


async def test_sinal_antes_de_comecar_nao_pega_nada() -> None:
    parar = asyncio.Event()
    parar.set()

    async def explode() -> Processado | None:
        raise AssertionError("nao deveria ser chamado")

    await executar_worker(processar=explode, recuperar=explode, parar=parar, intervalo_s=60)


async def test_sinal_durante_o_recover_nao_pega_job_novo() -> None:
    parar = asyncio.Event()

    async def recuperar() -> Processado | None:
        parar.set()
        return _FEITO

    async def explode() -> Processado | None:
        raise AssertionError("nao deveria processar depois do sinal")

    await asyncio.wait_for(
        executar_worker(processar=explode, recuperar=recuperar, parar=parar, intervalo_s=60), 1
    )


async def test_excecao_numa_volta_e_logada_e_o_laco_segue() -> None:
    parar = asyncio.Event()
    voltas = 0

    async def nada() -> Processado | None:
        return None

    async def processar() -> Processado | None:
        nonlocal voltas
        voltas += 1
        if voltas == 1:
            raise ConnectionError("banco caiu: segredo")
        parar.set()
        return _FEITO

    with capture_logs() as logs:
        await asyncio.wait_for(
            executar_worker(processar=processar, recuperar=nada, parar=parar, intervalo_s=0.01), 1
        )
    assert voltas == 2
    assert [dict(x) for x in logs if x["event"] == "volta_do_worker_falhou"] == [
        {"event": "volta_do_worker_falhou", "log_level": "error", "error_class": "ConnectionError"}
    ]
    assert "segredo" not in repr(logs)
