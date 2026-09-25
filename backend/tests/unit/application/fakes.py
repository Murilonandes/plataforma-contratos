"""Fakes em memoria das portas (``app/application/ports.py``).

Semantica de transacao: cada ``FakeUnitOfWork`` trabalha numa copia do estado
do ``BancoEmMemoria``; so o ``commit`` publica. Sair sem ``commit`` (ou por
excecao) descarta tudo. Uso sequencial: concorrencia real (SKIP LOCKED entre
transacoes abertas) e coberta pelos testes de integracao com Postgres.

``test_contrato_portas.py`` e o contrato que estes fakes (e os repositorios reais)
precisam cumprir.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from app.application import ports
from app.application.ports import (
    ChaveEmUso,
    ConflitoDeVersao,
    ContratoRegistro,
    DesfechoCsrf,
    DesfechoPost,
    EnvioRegistrado,
    EventoRegistrado,
    JobPego,
    NovoContrato,
    NovoJob,
    RegistroImutavel,
    ResultadoEnvio,
    SnapshotContrato,
)
from app.domain.enums import ContractStatus
from app.domain.states import Transicao


@dataclass
class _Job:
    novo: NovoJob
    ordem: int
    tentativas: int = 0
    lock_token: UUID | None = None
    locked_until: datetime | None = None
    concluido: bool = False
    ultimo_erro: str | None = None

    def pego(self) -> JobPego:
        assert self.lock_token is not None
        assert self.locked_until is not None
        return JobPego(
            id=self.novo.id,
            contract_id=self.novo.contract_id,
            snapshot_id=self.novo.snapshot_id,
            tentativa=self.tentativas,
            lock_token=self.lock_token,
            locked_until=self.locked_until,
            correlation_id=self.novo.correlation_id,
        )


@dataclass
class _Estado:
    contratos: dict[UUID, ContratoRegistro] = field(default_factory=dict)
    chaves: dict[UUID, tuple[str, str | None]] = field(default_factory=dict)  # (idem, pedido)
    snapshots: dict[UUID, SnapshotContrato] = field(default_factory=dict)
    eventos: dict[UUID, list[EventoRegistrado]] = field(default_factory=dict)
    jobs: dict[UUID, _Job] = field(default_factory=dict)
    envios: dict[UUID, EnvioRegistrado] = field(default_factory=dict)
    respostas: dict[UUID, ResultadoEnvio] = field(default_factory=dict)
    heartbeat_csrf: datetime | None = None

    def copiar(self) -> _Estado:
        """Copia os containers e os jobs (mutaveis); os demais valores sao imutaveis."""
        return _Estado(
            contratos=dict(self.contratos),
            chaves=dict(self.chaves),
            snapshots=dict(self.snapshots),
            eventos={k: list(v) for k, v in self.eventos.items()},
            jobs={k: copy.copy(v) for k, v in self.jobs.items()},
            envios=dict(self.envios),
            respostas=dict(self.respostas),
            heartbeat_csrf=self.heartbeat_csrf,
        )


class BancoEmMemoria:
    """Estado commitado, compartilhado pelas unidades de trabalho."""

    def __init__(self) -> None:
        self.estado = _Estado()


_INATIVOS = (ContractStatus.ERRO_NEGOCIO, ContractStatus.CANCELADO)


class _Contratos:
    def __init__(self, e: _Estado) -> None:
        self._e = e

    def _pedido_em_uso(self, pedido: str | None, *, exceto: UUID, status: ContractStatus) -> bool:
        if pedido is None or status in _INATIVOS:
            return False
        return any(
            cid != exceto and p == pedido and self._e.contratos[cid].status not in _INATIVOS
            for cid, (_, p) in self._e.chaves.items()
        )

    async def inserir(self, novo: NovoContrato) -> None:
        if novo.id in self._e.contratos:
            raise RegistroImutavel(f"contrato {novo.id} ja existe")
        pedido = novo.pedido_sysfertil or None  # vazio vira nulo (§6)
        if any(idem == novo.idempotency_key for idem, _ in self._e.chaves.values()):
            raise ChaveEmUso("idempotency_key")
        if self._pedido_em_uso(pedido, exceto=novo.id, status=ContractStatus.RASCUNHO):
            raise ChaveEmUso("pedido_sysfertil")
        self._e.contratos[novo.id] = ContratoRegistro(
            id=novo.id, status=ContractStatus.RASCUNHO, version=1, sap_contract_number=None
        )
        self._e.chaves[novo.id] = (novo.idempotency_key, pedido)

    async def obter(self, contract_id: UUID) -> ContratoRegistro | None:
        return self._e.contratos.get(contract_id)

    async def atualizar_status(
        self,
        contract_id: UUID,
        *,
        version_esperada: int,
        para: ContractStatus,
        sap_contract_number: str | None = None,
    ) -> ContratoRegistro:
        atual = self._e.contratos[contract_id]
        if atual.version != version_esperada:
            raise ConflitoDeVersao(f"versao {atual.version}, esperada {version_esperada}")
        if self._pedido_em_uso(self._e.chaves[contract_id][1], exceto=contract_id, status=para):
            raise ChaveEmUso("pedido_sysfertil")
        novo = ContratoRegistro(
            id=contract_id,
            status=para,
            version=atual.version + 1,
            sap_contract_number=sap_contract_number or atual.sap_contract_number,
        )
        self._e.contratos[contract_id] = novo
        return novo


class _Snapshots:
    def __init__(self, e: _Estado) -> None:
        self._e = e

    async def gravar(self, snapshot: SnapshotContrato) -> None:
        if snapshot.id in self._e.snapshots:
            raise RegistroImutavel(f"snapshot {snapshot.id} ja existe")
        self._e.snapshots[snapshot.id] = snapshot

    async def obter(self, snapshot_id: UUID) -> SnapshotContrato | None:
        return self._e.snapshots.get(snapshot_id)


class _Eventos:
    def __init__(self, e: _Estado) -> None:
        self._e = e

    async def anexar(
        self, contract_id: UUID, transicao: Transicao, *, occurred_at: datetime
    ) -> None:
        self._e.eventos.setdefault(contract_id, []).append(
            EventoRegistrado(transicao=transicao, occurred_at=occurred_at)
        )

    async def listar(self, contract_id: UUID) -> Sequence[EventoRegistrado]:
        return tuple(self._e.eventos.get(contract_id, ()))


class _Outbox:
    def __init__(self, e: _Estado) -> None:
        self._e = e

    async def enfileirar(self, job: NovoJob) -> None:
        if job.id in self._e.jobs:
            raise RegistroImutavel(f"job {job.id} ja existe")
        self._e.jobs[job.id] = _Job(novo=job, ordem=len(self._e.jobs))

    def _primeiro(self, candidatos: list[_Job]) -> _Job | None:
        candidatos.sort(key=lambda j: (j.novo.run_after, j.ordem))
        return candidatos[0] if candidatos else None

    async def pegar_proximo(self, *, agora: datetime, lock_ate: datetime) -> JobPego | None:
        job = self._primeiro(
            [
                j
                for j in self._e.jobs.values()
                if not j.concluido and j.lock_token is None and j.novo.run_after <= agora
            ]
        )
        if job is None:
            return None
        job.tentativas += 1
        job.lock_token, job.locked_until = uuid4(), lock_ate
        return job.pego()

    async def reivindicar_expirado(self, *, agora: datetime, lock_ate: datetime) -> JobPego | None:
        job = self._primeiro(
            [
                j
                for j in self._e.jobs.values()
                if not j.concluido
                and j.lock_token is not None
                and j.locked_until is not None
                and j.locked_until < agora
            ]
        )
        if job is None:
            return None
        job.lock_token, job.locked_until = uuid4(), lock_ate
        return job.pego()

    def _com_token(self, job_id: UUID, lock_token: UUID) -> _Job | None:
        job = self._e.jobs.get(job_id)
        if job is None or job.concluido or job.lock_token != lock_token:
            return None
        return job

    async def confirmar_lock(self, job_id: UUID, *, lock_token: UUID) -> bool:
        return self._com_token(job_id, lock_token) is not None

    async def reagendar(
        self, job_id: UUID, *, lock_token: UUID, run_after: datetime, ultimo_erro: str
    ) -> bool:
        job = self._com_token(job_id, lock_token)
        if job is None:
            return False
        job.novo = NovoJob(
            id=job.novo.id,
            contract_id=job.novo.contract_id,
            snapshot_id=job.novo.snapshot_id,
            run_after=run_after,
            correlation_id=job.novo.correlation_id,
        )
        job.lock_token = job.locked_until = None
        job.ultimo_erro = ultimo_erro
        return True

    async def concluir(self, job_id: UUID, *, lock_token: UUID) -> bool:
        job = self._com_token(job_id, lock_token)
        if job is None:
            return False
        job.concluido = True
        job.lock_token = job.locked_until = None
        return True


class _Envios:
    def __init__(self, e: _Estado) -> None:
        self._e = e

    async def registrar_envio(self, envio: EnvioRegistrado) -> None:
        if envio.id in self._e.envios:
            raise RegistroImutavel(f"envio {envio.id} ja registrado")
        self._e.envios[envio.id] = envio

    async def existe_para(self, job_id: UUID, *, tentativa: int) -> bool:
        return any(e.job_id == job_id and e.tentativa == tentativa for e in self._e.envios.values())

    async def obter(self, envio_id: UUID) -> EnvioRegistrado | None:
        return self._e.envios.get(envio_id)

    async def registrar_resposta(self, envio_id: UUID, resultado: ResultadoEnvio) -> None:
        if envio_id in self._e.respostas:
            raise RegistroImutavel(f"envio {envio_id} ja tem resposta")
        self._e.respostas[envio_id] = resultado

    async def resposta(self, envio_id: UUID) -> ResultadoEnvio | None:
        return self._e.respostas.get(envio_id)


class _Heartbeat:
    def __init__(self, e: _Estado) -> None:
        self._e = e

    async def registrar_csrf_ok(self, *, agora: datetime) -> None:
        self._e.heartbeat_csrf = agora

    async def ultimo_csrf_ok(self) -> datetime | None:
        return self._e.heartbeat_csrf


class FakeUnitOfWork:
    def __init__(self, banco: BancoEmMemoria) -> None:
        self._banco = banco
        self._estado = banco.estado.copiar()
        self.contratos = _Contratos(self._estado)
        self.snapshots = _Snapshots(self._estado)
        self.eventos = _Eventos(self._estado)
        self.outbox = _Outbox(self._estado)
        self.envios = _Envios(self._estado)
        self.heartbeat = _Heartbeat(self._estado)
        self.commits = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None  # sem commit: a copia e descartada

    async def commit(self) -> None:
        self._banco.estado = self._estado.copiar()
        self.commits += 1


class FakeClock:
    """Relogio controlado pelo teste (D5)."""

    def __init__(self, inicio: datetime) -> None:
        self._agora = inicio

    def agora(self) -> datetime:
        return self._agora

    def avancar(self, delta: timedelta) -> None:
        self._agora += delta


@dataclass
class FakeMetrics:
    """Conta as chamadas da porta ``Metrics`` (D15: exatamente uma por alerta)."""

    chamadas: list[tuple[str, dict[str, str | int]]] = field(default_factory=list)

    def incrementar(self, evento: str, **labels: str | int) -> None:
        self.chamadas.append((evento, labels))


@dataclass
class FakeGateway:
    """``SapContractGateway`` roteirizado: desfechos consumidos em ordem.

    ``csrf`` vazio = token ok. ``post`` pode ter uma excecao (caos: a porta promete
    nao levantar, o caso de uso nao confia). ``corpos`` guarda os bytes enviados.
    """

    csrf: list[DesfechoCsrf] = field(default_factory=list)
    post: list[DesfechoPost | Exception] = field(default_factory=list)
    corpos: list[bytes] = field(default_factory=list)
    preparos: int = 0

    async def preparar(self, *, correlation_id: str, contract_id: UUID) -> DesfechoCsrf:
        self.preparos += 1
        return self.csrf.pop(0) if self.csrf else DesfechoCsrf.ok()

    async def criar_contrato(
        self, corpo: bytes, *, correlation_id: str, contract_id: UUID
    ) -> DesfechoPost:
        self.corpos.append(corpo)
        item = self.post.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _conformidade(banco: BancoEmMemoria, inicio: datetime) -> None:
    """So para o mypy: os fakes cumprem as portas com as mesmas assinaturas."""
    uow: ports.UnitOfWork = FakeUnitOfWork(banco)
    relogio: ports.Clock = FakeClock(inicio)
    metricas: ports.Metrics = FakeMetrics()
    gateway: ports.SapContractGateway = FakeGateway()
    del uow, relogio, metricas, gateway
