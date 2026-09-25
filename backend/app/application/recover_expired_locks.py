"""Caso de uso ``recover_expired_locks`` (§4, D4). Tarefa 2.8.

Job com ``locked_until`` vencido: o worker morreu (ou travou) no meio da
tentativa. O recover reivindica o job com token novo (fencing: a partir dai nada
do worker antigo grava) e decide pelo marcador ``request_sent_at``:

- **sem** linha em ``contract_submissions`` para a tentativa ->
  ``LOCK_EXPIRADO_SEM_ENVIO`` -> ``NA_FILA`` (o POST nao saiu; job reagendado);
- **com** linha -> ``LOCK_EXPIRADO_COM_ENVIO`` -> ``INCERTO`` (pode ter chegado ao
  SAP; job concluido, alerta D15).

Ator ``system``. Um job por chamada; o loop do worker chama ate devolver ``None``.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from app.application.ports import Clock, Metrics, UnitOfWork
from app.application.process_outbox_job import Processado, alertar
from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.states import Ator, transition

_log = structlog.get_logger("app.worker")

ATOR_RECOVER = Ator(ActorKind.SYSTEM, "recover")


async def recuperar_lock_expirado(
    *,
    nova_uow: Callable[[], UnitOfWork],
    relogio: Clock,
    metricas: Metrics,
) -> Processado | None:
    async with nova_uow() as uow:
        agora = relogio.agora()
        # O job sai DESTA transacao concluido ou reagendado (lock limpo), e ate o commit
        # a linha fica travada (FOR UPDATE): o prazo do lock do recover nao importa.
        job = await uow.outbox.reivindicar_expirado(agora=agora, lock_ate=agora)
        if job is None:
            return None
        registro = await uow.contratos.obter(job.contract_id)
        if registro is None or registro.status is not ContractStatus.ENVIANDO:
            await uow.outbox.concluir(job.id, lock_token=job.lock_token)
            await uow.commit()
            _log.warning(
                "recover_job_descartado",
                job_id=str(job.id),
                contract_id=str(job.contract_id),
                status=None if registro is None else registro.status.value,
            )
            return Processado(job, None, None)
        enviado = await uow.envios.existe_para(job.id, tentativa=job.tentativa)
        t = transition(
            ContractStatus.ENVIANDO,
            (
                TransitionEvent.LOCK_EXPIRADO_COM_ENVIO
                if enviado
                else TransitionEvent.LOCK_EXPIRADO_SEM_ENVIO
            ),
            ator=ATOR_RECOVER,
            detalhe={"tentativa": job.tentativa},
        )
        await uow.contratos.atualizar_status(
            job.contract_id, version_esperada=registro.version, para=t.para
        )
        await uow.eventos.anexar(job.contract_id, t, occurred_at=agora)
        if enviado:
            await uow.outbox.concluir(job.id, lock_token=job.lock_token)
        else:
            await uow.outbox.reagendar(
                job.id, lock_token=job.lock_token, run_after=agora, ultimo_erro=t.evento.value
            )
        await uow.commit()
    _log.warning(
        "lock_expirado_recuperado",
        contract_id=str(job.contract_id),
        job_id=str(job.id),
        evento=t.evento.value,
    )
    alertar(metricas, t, job)
    return Processado(job, t.evento, t.para)
