"""Caso de uso ``submeter_contrato`` (D1, D6'). Tarefa 2.8.

``RASCUNHO``/``ERRO_NEGOCIO`` -> ``NA_FILA``, numa transacao so: snapshot novo
(contrato completo, parcelas calculadas no servidor, versao do algoritmo e a
entrada que as gerou), evento ``SUBMETER`` e job no outbox apontando para o
snapshot. A transicao e checada antes de validar o contrato (§4: par fora da
matriz e ``InvalidTransitionError`` antes de olhar qualquer dado). Qualquer erro
(dominio, transicao, ``ChaveEmUso``) sai sem gravar nada.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.application.ports import (
    Clock,
    EntradaParcelas,
    NovoJob,
    SnapshotContrato,
    UnitOfWork,
)
from app.application.snapshot import ALGORITMO_ATUAL, Algoritmo, montar_contrato
from app.domain.enums import TransitionEvent
from app.domain.politica import POLITICA_PADRAO, PoliticaSalesOrg
from app.domain.states import Ator, Transicao, transition


class ContratoNaoEncontrado(LookupError):
    def __init__(self, contract_id: UUID) -> None:
        self.contract_id = contract_id
        super().__init__(f"contrato {contract_id} nao encontrado")

    def __str__(self) -> str:  # LookupError poe aspas no str() de um argumento so
        return str(self.args[0])


@dataclass(frozen=True, slots=True)
class PedidoSubmissao:
    contract_id: UUID
    entrada: Mapping[str, object]  # formato de entrada do dominio, SEM to_FormPag
    parcelas: EntradaParcelas
    ator: Ator
    correlation_id: str
    justificativa: str | None = None


@dataclass(frozen=True, slots=True)
class Submetido:
    snapshot_id: UUID
    job_id: UUID
    transicao: Transicao


async def submeter_contrato(
    pedido: PedidoSubmissao,
    *,
    nova_uow: Callable[[], UnitOfWork],
    relogio: Clock,
    algoritmo: Algoritmo = ALGORITMO_ATUAL,
    politica: Mapping[str, PoliticaSalesOrg] = POLITICA_PADRAO,
) -> Submetido:
    async with nova_uow() as uow:
        registro = await uow.contratos.obter(pedido.contract_id)
        if registro is None:
            raise ContratoNaoEncontrado(pedido.contract_id)
        t = transition(
            registro.status,
            TransitionEvent.SUBMETER,
            ator=pedido.ator,
            justificativa=pedido.justificativa,
        )
        contrato = montar_contrato(
            pedido.entrada, pedido.parcelas, algoritmo=algoritmo, politica=politica
        )
        agora = relogio.agora()
        snapshot = SnapshotContrato(
            id=uuid4(),
            contract_id=pedido.contract_id,
            contrato=contrato,
            algoritmo_parcelas=algoritmo.versao,
            entrada_parcelas=pedido.parcelas,
        )
        job = NovoJob(
            id=uuid4(),
            contract_id=pedido.contract_id,
            snapshot_id=snapshot.id,
            run_after=agora,
            correlation_id=pedido.correlation_id,
        )
        await uow.snapshots.gravar(snapshot)
        await uow.contratos.atualizar_status(
            pedido.contract_id, version_esperada=registro.version, para=t.para
        )
        await uow.eventos.anexar(pedido.contract_id, t, occurred_at=agora)
        await uow.outbox.enfileirar(job)
        await uow.commit()
    return Submetido(snapshot_id=snapshot.id, job_id=job.id, transicao=t)
