"""Caso de uso ``process_outbox_job`` (D2, D3, D4, D11, D12, D13, D15). Tarefa 2.8.

Tres commits por tentativa (D2):

1. **Pega** o job (``SKIP LOCKED``, token novo) e aplica ``WORKER_PEGOU``
   (``conferencia=pulada_versao`` no detalhe se o snapshot for de outra versao
   do algoritmo, com log ``WARNING``).
   Depois, sem transacao: confere as parcelas (D11) e monta o corpo a partir do
   snapshot congelado. Falha aqui -> ``FALHA_NAO_CLASSIFICADA_ANTES_ENVIO``;
   divergencia -> ``CONFERENCIA_DIVERGENTE``. Em seguida o CSRF: falha ->
   ``FALHA_ANTES_POST`` (retry com backoff, ou ``_ESGOTOU`` no teto) ou
   ``SAP_4XX_TECNICO`` (401/403, sem retry).
2. **Marcador:** confere o lock e grava ``contract_submissions`` com os bytes
   exatos (``request_sent_at``). So entao o POST.
3. **Resultado:** confere o lock (fencing D4: perdeu -> nao grava nada, o recover
   decide), grava a resposta, aplica a transicao, grava o evento e conclui ou
   reagenda o job. Transicao que falha com a resposta lida -> ``FALHA_APOS_RESPOSTA``.

Qualquer excecao entre o commit do marcador e o commit do resultado vira
``FALHA_NAO_CLASSIFICADA_APOS_ENVIO`` (-> ``INCERTO``) numa transacao nova; se ate
isso falhar, o lock expira e o recover aplica ``LOCK_EXPIRADO_COM_ENVIO``. Nunca
``NA_FILA`` depois do marcador, salvo ``FALHA_ANTES_POST`` (o body nao saiu, §4).

Alerta (D15): cada transicao para ``INCERTO``/``ERRO_TECNICO`` chama
``Metrics.incrementar`` exatamente uma vez, depois do commit.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum

import structlog

from app.application.ports import (
    Clock,
    ContratoRegistro,
    DesfechoCsrf,
    DesfechoPost,
    EnvioRegistrado,
    JobPego,
    Metrics,
    ResultadoEnvio,
    SapContractGateway,
    SnapshotContrato,
    UnitOfWork,
)
from app.application.snapshot import ALGORITMO_ATUAL, Algoritmo, Divergencia, conferir
from app.domain.contract import Contract
from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.states import Ator, Transicao, transition

S = ContractStatus
E = TransitionEvent

_log = structlog.get_logger("app.worker")


class SnapshotAusente(LookupError):
    """O job aponta para um snapshot que nao existe (a FK impede; defesa)."""


class LockPerdidoNaTransacao(RuntimeError):
    """``concluir``/``reagendar`` nao acharam o token depois do ``confirmar_lock``."""


class PontoCaos(Enum):
    """Pontos de injecao de falha entre o marcador e o commit do resultado (D13)."""

    ANTES_DO_POST = "antes_do_post"
    DEPOIS_DO_POST = "depois_do_post"
    ANTES_DE_GRAVAR_RESPOSTA = "antes_de_gravar_resposta"
    ANTES_DA_TRANSICAO = "antes_da_transicao"
    ANTES_DO_EVENTO = "antes_do_evento"
    ANTES_DO_JOB = "antes_do_job"
    ANTES_DO_COMMIT = "antes_do_commit"
    NO_FALLBACK = "no_fallback"


Gancho = Callable[[PontoCaos], Awaitable[None]]


async def _sem_gancho(_: PontoCaos) -> None:
    return None


@dataclass(frozen=True, slots=True)
class ConfigWorker:
    worker_id: str
    lock_timeout: timedelta
    max_tentativas: int
    backoff_base_s: int = 30
    backoff_teto_s: int = 1800
    jitter: float = 0.2


def atraso_retry(tentativa: int, config: ConfigWorker, sorteio: float) -> timedelta:
    """D3: ``base x 2^(tentativa-1)``, teto, jitter de +-``jitter`` (``sorteio`` em [0, 1)).

    Segundos inteiros: ``<<`` com ``int`` nao estoura para tentativa alta.
    """
    segundos = min(config.backoff_base_s << (tentativa - 1), config.backoff_teto_s)
    fator: float = 1 + config.jitter * (2 * sorteio - 1)
    return timedelta(seconds=segundos) * fator


@dataclass(frozen=True, slots=True)
class Processado:
    job: JobPego
    evento: TransitionEvent | None  # None: job descartado ou lock perdido (nada gravado)
    para: ContractStatus | None

    def __post_init__(self) -> None:
        if not isinstance(self.job, JobPego):
            raise TypeError("Processado.job precisa ser JobPego")
        if (self.evento is None) != (self.para is None):
            raise ValueError("Processado: evento e para vem juntos")


def nome_do_alerta(t: Transicao) -> str | None:
    if t.evento is E.CONFERENCIA_DIVERGENTE:
        return "conferencia_divergente"
    if t.para is S.INCERTO:
        return "contrato_incerto"
    if t.para is S.ERRO_TECNICO:
        return "erro_tecnico"
    return None


def alertar(metricas: Metrics, t: Transicao, job: JobPego) -> None:
    nome = nome_do_alerta(t)
    if nome is not None:
        metricas.incrementar(nome, transicao=t.evento.value, contract_id=str(job.contract_id))


def _ultimo_erro(evento: TransitionEvent, detalhe: Mapping[str, str | int]) -> str:
    return " ".join([evento.value, *(f"{k}={v}" for k, v in sorted(detalhe.items()))])


@dataclass
class ProcessadorOutbox:
    nova_uow: Callable[[], UnitOfWork]
    relogio: Clock
    gateway: SapContractGateway
    metricas: Metrics
    config: ConfigWorker
    corpo_de: Callable[[Contract], bytes]  # mapper + to_json (infra), injetado
    algoritmo: Algoritmo = ALGORITMO_ATUAL
    sorteio: Callable[[], float] = random.random
    gancho: Gancho = field(default=_sem_gancho)

    @property
    def _ator(self) -> Ator:
        return Ator(ActorKind.WORKER, self.config.worker_id)

    async def processar_proximo(self) -> Processado | None:
        pego = await self._pegar()
        if pego is None:
            return None
        job, registro, snapshot, falha_snapshot = pego
        if registro is None:
            return Processado(job, None, None)
        log = _log.bind(
            correlation_id=job.correlation_id,
            contract_id=str(job.contract_id),
            job_id=str(job.id),
            tentativa=job.tentativa,
        )

        try:
            if falha_snapshot is not None:
                raise falha_snapshot
            if snapshot is None:
                raise SnapshotAusente
            conferencia = conferir(snapshot, self.algoritmo)
            corpo = self.corpo_de(snapshot.contrato)
        except Exception as exc:
            log.error("preparo_falhou", error_class=type(exc).__name__)
            return await self._finalizar_sem_envio(
                job,
                registro,
                E.FALHA_NAO_CLASSIFICADA_ANTES_ENVIO,
                {"fase": "preparo", "error_class": type(exc).__name__},
            )
        if isinstance(conferencia, Divergencia):
            log.error("conferencia_divergente", **conferencia.detalhe())
            return await self._finalizar_sem_envio(
                job, registro, E.CONFERENCIA_DIVERGENTE, conferencia.detalhe()
            )

        try:
            csrf = await self.gateway.preparar(
                correlation_id=job.correlation_id, contract_id=job.contract_id
            )
        except Exception as exc:  # a porta promete nao levantar; CSRF e antes do POST (§4)
            csrf = DesfechoCsrf(
                E.FALHA_ANTES_POST, {"fase": "csrf", "error_class": type(exc).__name__}
            )
        if csrf.evento is not None:
            return await self._finalizar_sem_envio(job, registro, csrf.evento, csrf.detalhe)

        envio = await self._marcar(job, snapshot, corpo, token_novo=csrf.token_novo)
        if envio is None:
            log.warning("lock_perdido", fase="marcador")
            return Processado(job, None, None)

        try:
            await self.gancho(PontoCaos.ANTES_DO_POST)
            try:
                desfecho = await self.gateway.criar_contrato(
                    corpo, correlation_id=job.correlation_id, contract_id=job.contract_id
                )
            except Exception as exc:  # a porta promete nao levantar; depois do marcador
                desfecho = DesfechoPost(
                    evento=E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO,
                    sap_contract_number=None,
                    resposta=None,
                    duracao_ms=0,
                    detalhe={"fase": "post", "error_class": type(exc).__name__},
                )
            await self.gancho(PontoCaos.DEPOIS_DO_POST)
            return await self._gravar_resultado(job, registro, envio, desfecho)
        except Exception as exc:
            log.error("falha_depois_do_marcador", error_class=type(exc).__name__)
            return await self._fallback_incerto(job, registro, exc)

    # -- commit 1 ----------------------------------------------------------------------

    async def _pegar(
        self,
    ) -> tuple[JobPego, ContratoRegistro | None, SnapshotContrato | None, Exception | None] | None:
        async with self.nova_uow() as uow:
            agora = self.relogio.agora()
            job = await uow.outbox.pegar_proximo(
                agora=agora, lock_ate=agora + self.config.lock_timeout
            )
            if job is None:
                return None
            registro = await uow.contratos.obter(job.contract_id)
            if registro is None or registro.status is not S.NA_FILA:
                await uow.outbox.concluir(job.id, lock_token=job.lock_token)
                await uow.commit()
                _log.warning(
                    "job_descartado",
                    job_id=str(job.id),
                    contract_id=str(job.contract_id),
                    status=None if registro is None else registro.status.value,
                )
                return job, None, None, None
            # Snapshot que nao reconstroi (regra de dominio mudou depois da submissao) NAO
            # pode derrubar esta transacao: o job voltaria destravado e travaria a fila.
            # A falha vai para o preparo (FALHA_NAO_CLASSIFICADA_ANTES_ENVIO, com alerta).
            falha: Exception | None = None
            try:
                snapshot = await uow.snapshots.obter(job.snapshot_id)
            except Exception as exc:
                snapshot, falha = None, exc
            detalhe: dict[str, str | int] = {"tentativa": job.tentativa}
            versao = None if snapshot is None else snapshot.algoritmo_parcelas
            pulada = versao not in (None, self.algoritmo.versao)
            if pulada:
                detalhe["conferencia"] = "pulada_versao"
            t = transition(S.NA_FILA, E.WORKER_PEGOU, ator=self._ator, detalhe=detalhe)
            registro = await uow.contratos.atualizar_status(
                job.contract_id, version_esperada=registro.version, para=t.para
            )
            await uow.eventos.anexar(job.contract_id, t, occurred_at=agora)
            await uow.commit()
        if pulada:
            _log.warning(
                "conferencia_pulada_versao",
                contract_id=str(job.contract_id),
                algoritmo_snapshot=versao,
                algoritmo_atual=self.algoritmo.versao,
            )
        return job, registro, snapshot, falha

    # -- commit 2 ----------------------------------------------------------------------

    async def _marcar(
        self, job: JobPego, snapshot: SnapshotContrato, corpo: bytes, *, token_novo: bool
    ) -> EnvioRegistrado | None:
        async with self.nova_uow() as uow:
            agora = self.relogio.agora()
            if not await uow.outbox.confirmar_lock(job.id, lock_token=job.lock_token):
                return None
            envio = EnvioRegistrado.criar(
                job_id=job.id,
                contract_id=job.contract_id,
                snapshot_id=snapshot.id,
                tentativa=job.tentativa,
                request_sent_at=agora,
                request_body=corpo,
            )
            await uow.envios.registrar_envio(envio)
            if token_novo:  # token do cache nao prova que o SAP responde agora
                await uow.heartbeat.registrar_csrf_ok(agora=agora)
            await uow.commit()
        return envio

    # -- commit 3 ----------------------------------------------------------------------

    def _evento_final(self, job: JobPego, evento: TransitionEvent) -> TransitionEvent:
        if evento is E.FALHA_ANTES_POST and job.tentativa >= self.config.max_tentativas:
            return E.FALHA_ANTES_POST_ESGOTOU
        return evento

    async def _aplicar(
        self,
        uow: UnitOfWork,
        job: JobPego,
        registro: ContratoRegistro,
        t: Transicao,
        gancho: Gancho,
    ) -> None:
        agora = self.relogio.agora()
        await uow.contratos.atualizar_status(
            job.contract_id,
            version_esperada=registro.version,
            para=t.para,
            sap_contract_number=t.sap_contract_number,
        )
        await gancho(PontoCaos.ANTES_DO_EVENTO)
        await uow.eventos.anexar(job.contract_id, t, occurred_at=agora)
        await gancho(PontoCaos.ANTES_DO_JOB)
        if t.evento is E.FALHA_ANTES_POST:
            sorteio = self.sorteio()
            ok = await uow.outbox.reagendar(
                job.id,
                lock_token=job.lock_token,
                run_after=agora + atraso_retry(job.tentativa, self.config, sorteio),
                ultimo_erro=_ultimo_erro(t.evento, t.detalhe),
            )
        else:
            ok = await uow.outbox.concluir(job.id, lock_token=job.lock_token)
        if not ok:
            raise LockPerdidoNaTransacao
        await gancho(PontoCaos.ANTES_DO_COMMIT)
        await uow.commit()

    async def _finalizar_sem_envio(
        self,
        job: JobPego,
        registro: ContratoRegistro,
        evento: TransitionEvent,
        detalhe: Mapping[str, str | int],
    ) -> Processado:
        """Desfecho antes do marcador: nada foi enviado."""
        t = transition(
            S.ENVIANDO,
            self._evento_final(job, evento),
            ator=self._ator,
            detalhe={**detalhe, "tentativa": job.tentativa},
        )
        async with self.nova_uow() as uow:
            if not await uow.outbox.confirmar_lock(job.id, lock_token=job.lock_token):
                _log.warning("lock_perdido", fase="sem_envio", job_id=str(job.id))
                return Processado(job, None, None)
            await self._aplicar(uow, job, registro, t, _sem_gancho)
        alertar(self.metricas, t, job)
        return Processado(job, t.evento, t.para)

    async def _gravar_resultado(
        self,
        job: JobPego,
        registro: ContratoRegistro,
        envio: EnvioRegistrado,
        d: DesfechoPost,
    ) -> Processado:
        async with self.nova_uow() as uow:
            if not await uow.outbox.confirmar_lock(job.id, lock_token=job.lock_token):
                _log.warning("lock_perdido", fase="resultado", job_id=str(job.id))
                return Processado(job, None, None)
            await self.gancho(PontoCaos.ANTES_DE_GRAVAR_RESPOSTA)
            if d.resposta is not None and d.resposta.status < 500:  # o SAP respondeu
                await uow.heartbeat.registrar_csrf_ok(agora=self.relogio.agora())
            error_class = d.detalhe.get("error_class")
            await uow.envios.registrar_resposta(
                envio.id,
                ResultadoEnvio(
                    response_status=d.resposta.status if d.resposta else None,
                    response_body=d.resposta.corpo if d.resposta else None,
                    mensagens=d.mensagens,
                    duracao_ms=d.duracao_ms,
                    error_class=None if error_class is None else str(error_class),
                ),
            )
            await self.gancho(PontoCaos.ANTES_DA_TRANSICAO)
            try:
                t = transition(
                    S.ENVIANDO,
                    self._evento_final(job, d.evento),
                    ator=self._ator,
                    sap_contract_number=d.sap_contract_number,
                    detalhe={**d.detalhe, "tentativa": job.tentativa},
                )
            except Exception as exc:  # D12: nosso processamento falhou depois do envio
                t = transition(
                    S.ENVIANDO,
                    E.FALHA_APOS_RESPOSTA if d.resposta else E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO,
                    ator=self._ator,
                    detalhe={
                        "fase": "transicao",
                        "error_class": type(exc).__name__,
                        "tentativa": job.tentativa,
                    },
                )
            await self._aplicar(uow, job, registro, t, self.gancho)
        alertar(self.metricas, t, job)
        return Processado(job, t.evento, t.para)

    async def _fallback_incerto(
        self, job: JobPego, registro: ContratoRegistro, exc: Exception
    ) -> Processado:
        """Excecao depois do marcador: ``INCERTO`` direto; se falhar, o recover decide."""
        t = transition(
            S.ENVIANDO,
            E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO,
            ator=self._ator,
            detalhe={
                "fase": "pos_envio",
                "error_class": type(exc).__name__,
                "tentativa": job.tentativa,
            },
        )
        try:
            await self.gancho(PontoCaos.NO_FALLBACK)
            async with self.nova_uow() as uow:
                if not await uow.outbox.confirmar_lock(job.id, lock_token=job.lock_token):
                    return Processado(job, None, None)
                await self._aplicar(uow, job, registro, t, _sem_gancho)
        except Exception as exc2:
            _log.error(
                "fallback_incerto_falhou",
                contract_id=str(job.contract_id),
                job_id=str(job.id),
                error_class=type(exc2).__name__,
            )
            return Processado(job, None, None)
        alertar(self.metricas, t, job)
        return Processado(job, t.evento, t.para)
