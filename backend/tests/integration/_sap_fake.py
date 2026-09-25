"""SAP fake (respx) e montagem comum dos testes de integracao do worker (Tarefa 2.9)."""

from __future__ import annotations

import itertools
import json
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import respx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.ports import Clock, NovoContrato, UnitOfWork
from app.application.process_outbox_job import ConfigWorker, Gancho, ProcessadorOutbox
from app.application.submit_contract import PedidoSubmissao, submeter_contrato
from app.infrastructure.db.uow import fabrica_de_uow
from app.infrastructure.sap.client import ClienteSap, ConfigSap
from app.infrastructure.sap.gateway import GatewaySap
from tests.unit.application._cenario import PARCELAS, VENDEDOR, corpo_sap, entrada_sem_parcelas
from tests.unit.application.fakes import FakeMetrics

BASE = "https://s4-dev.example/sap/opu/odata4/sap/zapi/0001/"
POST_URL = BASE + "CriaContrato"


def config_sap() -> ConfigSap:
    return ConfigSap(
        base_url=BASE,
        sap_client="300",
        usuario="USR_TEC",
        senha="senha-de-teste",
        timeout_connect_s=5.0,
        timeout_read_s=30.0,
        decimal_as_string=True,
    )


def montar_sap(router: respx.MockRouter) -> respx.Route:
    """Token CSRF ok e POST 201 com numero sequencial. Devolve a rota do POST."""
    numeros = itertools.count(40000001)
    router.get(BASE).mock(return_value=httpx.Response(200, headers={"x-csrf-token": "t"}))

    def criar(_: httpx.Request) -> httpx.Response:
        corpo = json.dumps({"SalesContract": str(next(numeros))}).encode()
        return httpx.Response(201, content=corpo)

    return router.post(POST_URL).mock(side_effect=criar)


async def contrato_na_fila(nova_uow: object, relogio: Clock) -> UUID:
    fabrica = nova_uow  # Callable[[], UnitOfWork]
    cid = uuid4()
    uow: UnitOfWork
    async with fabrica() as uow:  # type: ignore[operator]
        await uow.contratos.inserir(
            NovoContrato(
                id=cid,
                origin="API",
                created_by="oid",
                idempotency_key=str(uuid4()),
                pedido_sysfertil=None,
                entrada={},
            )
        )
        await uow.commit()
    await submeter_contrato(
        PedidoSubmissao(
            contract_id=cid,
            entrada=entrada_sem_parcelas(),
            parcelas=PARCELAS,
            ator=VENDEDOR,
            correlation_id=f"corr-{cid.hex[:8]}",
        ),
        nova_uow=fabrica,  # type: ignore[arg-type]
        relogio=relogio,
    )
    return cid


def processador(
    engine: AsyncEngine,
    relogio: Clock,
    cliente: ClienteSap,
    worker_id: str,
    *,
    gancho: Gancho | None = None,
) -> ProcessadorOutbox:
    extra = {} if gancho is None else {"gancho": gancho}
    return ProcessadorOutbox(
        nova_uow=fabrica_de_uow(engine, relogio),
        relogio=relogio,
        gateway=GatewaySap(cliente),
        metricas=FakeMetrics(),
        config=ConfigWorker(
            worker_id=worker_id, lock_timeout=timedelta(seconds=600), max_tentativas=5
        ),
        corpo_de=corpo_sap,
        **extra,  # type: ignore[arg-type]
    )
