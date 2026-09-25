"""Montagem comum dos testes dos casos de uso (Tarefa 2.8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.application.ports import (
    DesfechoPost,
    EntradaParcelas,
    MensagemSap,
    NovoContrato,
    RespostaSap,
    UnitOfWork,
)
from app.application.process_outbox_job import (
    ConfigWorker,
    Gancho,
    Processado,
    ProcessadorOutbox,
)
from app.application.recover_expired_locks import recuperar_lock_expirado
from app.application.snapshot import ALGORITMO_ATUAL, Algoritmo
from app.application.submit_contract import PedidoSubmissao, Submetido, submeter_contrato
from app.domain.contract import Contract
from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.states import Ator
from app.infrastructure.sap.mapper import to_json, to_payload
from tests.unit.application.fakes import (
    BancoEmMemoria,
    FakeClock,
    FakeGateway,
    FakeMetrics,
    FakeUnitOfWork,
)
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
LOCK = timedelta(seconds=600)
VENDEDOR = Ator(ActorKind.USER, "oid-vendedor")

PARCELAS = EntradaParcelas(
    total=Decimal("23299.55"),
    pesos=(1, 1, 1),
    datas=(date(2026, 9, 4), date(2026, 10, 4), date(2026, 11, 3)),
    form_pag="K",
)


def entrada_sem_parcelas() -> dict[str, Any]:
    entrada = payload_exemplo_como_entrada()
    entrada.pop("to_FormPag")
    return entrada


@dataclass
class Cenario:
    banco: BancoEmMemoria = field(default_factory=BancoEmMemoria)
    relogio: FakeClock = field(default_factory=lambda: FakeClock(T0))
    metricas: FakeMetrics = field(default_factory=FakeMetrics)
    gateway: FakeGateway = field(default_factory=FakeGateway)

    def uow(self) -> UnitOfWork:
        return FakeUnitOfWork(self.banco)

    async def rascunho(self, *, pedido: str | None = None) -> UUID:
        contract_id = uuid4()
        async with self.uow() as uow:
            await uow.contratos.inserir(
                NovoContrato(
                    id=contract_id,
                    origin="API",
                    created_by="oid-vendedor",
                    idempotency_key=str(uuid4()),
                    pedido_sysfertil=pedido,
                    entrada={},
                )
            )
            await uow.commit()
        return contract_id

    async def forcar_status(self, contract_id: UUID, para: ContractStatus) -> None:
        async with self.uow() as uow:
            atual = await uow.contratos.obter(contract_id)
            assert atual is not None
            await uow.contratos.atualizar_status(
                contract_id, version_esperada=atual.version, para=para
            )
            await uow.commit()

    async def submeter(
        self, contract_id: UUID, *, entrada: dict[str, Any] | None = None
    ) -> Submetido:
        return await submeter_contrato(
            PedidoSubmissao(
                contract_id=contract_id,
                entrada=entrada if entrada is not None else entrada_sem_parcelas(),
                parcelas=PARCELAS,
                ator=VENDEDOR,
                correlation_id="corr-1",
            ),
            nova_uow=self.uow,
            relogio=self.relogio,
        )

    def processador(
        self,
        *,
        max_tentativas: int = 5,
        algoritmo: Algoritmo = ALGORITMO_ATUAL,
        gancho: Gancho | None = None,
        corpo_de: Any = None,
    ) -> ProcessadorOutbox:
        extra: dict[str, Any] = {} if gancho is None else {"gancho": gancho}
        return ProcessadorOutbox(
            nova_uow=self.uow,
            relogio=self.relogio,
            gateway=self.gateway,
            metricas=self.metricas,
            config=ConfigWorker(worker_id="w1", lock_timeout=LOCK, max_tentativas=max_tentativas),
            corpo_de=corpo_de or corpo_sap,
            algoritmo=algoritmo,
            sorteio=lambda: 0.5,  # jitter zero
            **extra,
        )

    async def recuperar(self) -> Processado | None:
        return await recuperar_lock_expirado(
            nova_uow=self.uow, relogio=self.relogio, metricas=self.metricas
        )

    async def eventos(self, contract_id: UUID) -> list[TransitionEvent]:
        async with self.uow() as uow:
            return [e.transicao.evento for e in await uow.eventos.listar(contract_id)]

    async def status(self, contract_id: UUID) -> ContractStatus:
        async with self.uow() as uow:
            registro = await uow.contratos.obter(contract_id)
        assert registro is not None
        return registro.status


def corpo_sap(contrato: Contract) -> bytes:
    return to_json(to_payload(contrato, decimal_as_string=True))


def desfecho(
    evento: TransitionEvent,
    *,
    status: int | None = None,
    corpo: bytes = b"",
    numero: str | None = None,
    mensagens: tuple[MensagemSap, ...] = (),
    detalhe: dict[str, str | int] | None = None,
) -> DesfechoPost:
    return DesfechoPost(
        evento=evento,
        sap_contract_number=numero,
        resposta=None if status is None else RespostaSap(status=status, headers={}, corpo=corpo),
        duracao_ms=12,
        mensagens=mensagens,
        detalhe=detalhe if detalhe is not None else {"fase": "post"},
    )
