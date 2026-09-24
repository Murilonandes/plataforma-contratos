"""Portas da aplicacao (``Protocol``) e os tipos de valor que atravessam elas.

Sem SQLAlchemy, httpx ou FastAPI: a infraestrutura implementa, os casos de uso
consomem. As garantias aprovadas da Fase 2 moram nos proprios tipos:

- **Relogio (D5):** todo metodo que compara tempo recebe ``agora`` por parametro
  (vindo do ``Clock``); nenhuma implementacao usa ``now()`` do banco. Instantes
  sempre com fuso.
- **Fencing (D4):** cada pega do outbox gera um ``lock_token`` novo; a recuperacao
  de lock expirado **rotaciona** o token. ``confirmar_lock``, ``reagendar`` e
  ``concluir`` so valem com o token atual. No commit 3, o caso de uso chama
  ``confirmar_lock`` primeiro (trava a linha do job e confere o token) e, se ele
  mudou, nao grava nada.
- **Marcador de envio (D7):** ``EnvioRegistrado`` guarda os bytes exatos do POST e
  o sha256 deles; o tipo recusa hash que nao bate.
- **Snapshot (D6'):** ``SnapshotContrato`` congelado na submissao, imutavel.
- **Gateway SAP:** nunca levanta excecao de transporte; devolve um desfecho ja
  classificado (§4), e o tipo so aceita eventos possiveis naquela fase.
- **Saude do SAP fora do ready:** so o ``HeartbeatRepo`` (ultimo CSRF ok).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType, TracebackType
from typing import Final, Literal, Protocol, Self
from uuid import UUID, uuid4

from app.domain.contract import Contract
from app.domain.enums import ContractStatus, TransitionEvent
from app.domain.states import Transicao

E = TransitionEvent

# Eventos que o gateway pode devolver em cada fase (§4, classificacao em runtime).
EVENTOS_CSRF: Final = frozenset(
    {E.FALHA_ANTES_POST, E.SAP_4XX_TECNICO, E.FALHA_NAO_CLASSIFICADA_ANTES_ENVIO}
)
EVENTOS_POST: Final = frozenset(
    {
        E.SAP_201,
        E.SAP_4XX_NEGOCIO,
        E.SAP_4XX_TECNICO,
        E.FALHA_ANTES_POST,  # ConnectError no POST: o body nao saiu (§4)
        E.TIMEOUT_APOS_POST,
        E.CONEXAO_CAIDA_APOS_POST,
        E.SAP_5XX_APOS_POST,
        E.FALHA_APOS_RESPOSTA,
        E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO,
    }
)


def _com_fuso(nome: str, valor: datetime) -> None:
    if valor.tzinfo is None or valor.utcoffset() is None:
        raise ValueError(f"{nome} precisa ter fuso horario")


def _congelar(valor: Mapping[str, str | int]) -> Mapping[str, str | int]:
    return MappingProxyType(dict(valor))


# ---- Erros ----------------------------------------------------------------------------


class ConflitoDeVersao(Exception):
    """Lock otimista: a versao do contrato mudou desde a leitura."""


class RegistroImutavel(Exception):
    """Tentativa de regravar snapshot, marcador de envio ou resposta ja gravados."""


# ---- Tipos de valor ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NovoContrato:
    id: UUID
    origin: Literal["WEB", "API"]
    created_by: str
    idempotency_key: str
    pedido_sysfertil: str | None  # vazio vira None na persistencia (§6)
    entrada: Mapping[str, object]  # rascunho no formato de entrada do dominio


@dataclass(frozen=True, slots=True)
class ContratoRegistro:
    id: UUID
    status: ContractStatus
    version: int
    sap_contract_number: str | None


@dataclass(frozen=True, slots=True)
class EntradaParcelas:
    """O que gerou as parcelas do snapshot (so para a conferencia, D6')."""

    total: Decimal
    pesos: tuple[int, ...]
    datas: tuple[date, ...]
    form_pag: str


@dataclass(frozen=True, slots=True)
class SnapshotContrato:
    """Contrato congelado na submissao, ja com as parcelas; imutavel (D6')."""

    id: UUID
    contract_id: UUID
    contrato: Contract
    algoritmo_parcelas: str
    entrada_parcelas: EntradaParcelas


@dataclass(frozen=True, slots=True)
class EventoRegistrado:
    transicao: Transicao
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class NovoJob:
    id: UUID
    contract_id: UUID
    snapshot_id: UUID
    run_after: datetime
    correlation_id: str

    def __post_init__(self) -> None:
        _com_fuso("run_after", self.run_after)


@dataclass(frozen=True, slots=True)
class JobPego:
    """Job travado por um worker (ou pelo recover), com o token da pega."""

    id: UUID
    contract_id: UUID
    snapshot_id: UUID
    tentativa: int
    lock_token: UUID
    locked_until: datetime
    correlation_id: str

    def __post_init__(self) -> None:
        _com_fuso("locked_until", self.locked_until)


@dataclass(frozen=True, slots=True)
class EnvioRegistrado:
    """Marcador ``request_sent_at``: gravado e commitado ANTES do POST (§4)."""

    id: UUID
    job_id: UUID
    contract_id: UUID
    snapshot_id: UUID
    tentativa: int
    request_sent_at: datetime
    request_body: bytes
    request_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.request_body, bytes):
            raise TypeError("request_body precisa ser bytes")
        if self.tentativa < 1:
            raise ValueError("tentativa precisa ser >= 1")
        _com_fuso("request_sent_at", self.request_sent_at)
        if hashlib.sha256(self.request_body).hexdigest() != self.request_sha256:
            raise ValueError("request_sha256 nao corresponde a request_body")

    @classmethod
    def criar(
        cls,
        *,
        job_id: UUID,
        contract_id: UUID,
        snapshot_id: UUID,
        tentativa: int,
        request_sent_at: datetime,
        request_body: bytes,
    ) -> EnvioRegistrado:
        return cls(
            id=uuid4(),
            job_id=job_id,
            contract_id=contract_id,
            snapshot_id=snapshot_id,
            tentativa=tentativa,
            request_sent_at=request_sent_at,
            request_body=request_body,
            request_sha256=hashlib.sha256(request_body).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class MensagemSap:
    code: str
    message: str
    target: str | None  # cru, como veio do SAP
    path: str | None  # mapeado para o path do dominio, quando possivel


@dataclass(frozen=True, slots=True)
class RespostaSap:
    """Resposta HTTP crua do SAP (corpo em bytes: pode nem ser JSON)."""

    status: int
    headers: Mapping[str, str]
    corpo: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class ResultadoEnvio:
    """O que o commit 3 grava na linha de ``contract_submissions``."""

    response_status: int | None
    response_body: bytes | None  # cru (TEXT na tabela; JSON invalido tambem, D12)
    mensagens: tuple[MensagemSap, ...]
    duracao_ms: int
    error_class: str | None


@dataclass(frozen=True, slots=True)
class DesfechoCsrf:
    """Resultado do fetch do token. ``evento`` ``None`` = token ok."""

    evento: TransitionEvent | None
    detalhe: Mapping[str, str | int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.evento is not None and self.evento not in EVENTOS_CSRF:
            raise ValueError(f"evento {self.evento.value} nao e possivel no CSRF")
        object.__setattr__(self, "detalhe", _congelar(self.detalhe))

    @classmethod
    def ok(cls) -> DesfechoCsrf:
        return cls(evento=None)


@dataclass(frozen=True, slots=True)
class DesfechoPost:
    """Resultado do POST (ja classificado pela §4), depois do marcador."""

    evento: TransitionEvent
    sap_contract_number: str | None
    resposta: RespostaSap | None  # None quando nao houve resposta (timeout, conexao...)
    duracao_ms: int
    mensagens: tuple[MensagemSap, ...] = ()
    detalhe: Mapping[str, str | int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.evento not in EVENTOS_POST:
            raise ValueError(f"evento {self.evento.value} nao e possivel depois do marcador")
        if self.evento is E.SAP_201 and self.sap_contract_number is None:
            raise ValueError("SAP_201 exige sap_contract_number")
        if self.evento is not E.SAP_201 and self.sap_contract_number is not None:
            raise ValueError("so SAP_201 tem sap_contract_number")
        if self.duracao_ms < 0:
            raise ValueError("duracao_ms precisa ser >= 0")
        object.__setattr__(self, "detalhe", _congelar(self.detalhe))


# ---- Portas -------------------------------------------------------------------------------


class Clock(Protocol):
    def agora(self) -> datetime:
        """Instante atual com fuso (UTC)."""
        ...


class Metrics(Protocol):
    def incrementar(self, evento: str, **labels: str | int) -> None:
        """D15: Fase 2 = log ERROR com ``alert``; Fase 5 = Prometheus."""
        ...


class ContractRepo(Protocol):
    async def inserir(self, novo: NovoContrato) -> None: ...

    async def obter(self, contract_id: UUID) -> ContratoRegistro | None: ...

    async def atualizar_status(
        self,
        contract_id: UUID,
        *,
        version_esperada: int,
        para: ContractStatus,
        sap_contract_number: str | None = None,
    ) -> ContratoRegistro:
        """Levanta ``ConflitoDeVersao`` se a versao nao for a esperada."""
        ...


class SnapshotRepo(Protocol):
    async def gravar(self, snapshot: SnapshotContrato) -> None:
        """Levanta ``RegistroImutavel`` se o id ja existe."""
        ...

    async def obter(self, snapshot_id: UUID) -> SnapshotContrato | None: ...


class EventRepo(Protocol):
    async def anexar(
        self, contract_id: UUID, transicao: Transicao, *, occurred_at: datetime
    ) -> None: ...

    async def listar(self, contract_id: UUID) -> Sequence[EventoRegistrado]: ...


class OutboxRepo(Protocol):
    async def enfileirar(self, job: NovoJob) -> None: ...

    async def pegar_proximo(self, *, agora: datetime, lock_ate: datetime) -> JobPego | None:
        """Job nao travado com ``run_after <= agora`` (SKIP LOCKED), com token novo e
        ``tentativa`` + 1. Job travado, mesmo vencido, e do ``reivindicar_expirado``."""
        ...

    async def reivindicar_expirado(self, *, agora: datetime, lock_ate: datetime) -> JobPego | None:
        """Job com ``locked_until < agora``: rotaciona o token (fencing), mesma tentativa."""
        ...

    async def confirmar_lock(self, job_id: UUID, *, lock_token: UUID) -> bool:
        """Trava a linha do job e confere o token (commit 3). ``False`` = perdeu o lock."""
        ...

    async def reagendar(
        self, job_id: UUID, *, lock_token: UUID, run_after: datetime, ultimo_erro: str
    ) -> bool: ...

    async def concluir(self, job_id: UUID, *, lock_token: UUID) -> bool: ...


class SubmissionRepo(Protocol):
    async def registrar_envio(self, envio: EnvioRegistrado) -> None:
        """Marcador. Levanta ``RegistroImutavel`` se ja existe."""
        ...

    async def existe_para(self, job_id: UUID, *, tentativa: int) -> bool: ...

    async def obter(self, envio_id: UUID) -> EnvioRegistrado | None: ...

    async def registrar_resposta(self, envio_id: UUID, resultado: ResultadoEnvio) -> None:
        """Uma vez so: levanta ``RegistroImutavel`` na segunda."""
        ...

    async def resposta(self, envio_id: UUID) -> ResultadoEnvio | None: ...


class HeartbeatRepo(Protocol):
    async def registrar_csrf_ok(self, *, agora: datetime) -> None: ...

    async def ultimo_csrf_ok(self) -> datetime | None: ...


class UnitOfWork(Protocol):
    """Uma transacao. Sem ``commit`` explicito, tudo e descartado ao sair."""

    # Somente leitura: o caso de uso usa, nunca troca um repositorio.
    @property
    def contratos(self) -> ContractRepo: ...
    @property
    def snapshots(self) -> SnapshotRepo: ...
    @property
    def eventos(self) -> EventRepo: ...
    @property
    def outbox(self) -> OutboxRepo: ...
    @property
    def envios(self) -> SubmissionRepo: ...
    @property
    def heartbeat(self) -> HeartbeatRepo: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...


class SapContractGateway(Protocol):
    """Adapter do ``CriaContrato``. Nunca levanta excecao de transporte."""

    async def preparar(self, *, correlation_id: str, contract_id: UUID) -> DesfechoCsrf:
        """Garante um token CSRF valido (fetch se nao houver em cache)."""
        ...

    async def criar_contrato(
        self, corpo: bytes, *, correlation_id: str, contract_id: UUID
    ) -> DesfechoPost:
        """POST com ``content=corpo`` (nunca ``json=``); 403 CSRF: refetch + 1 reenvio."""
        ...
