"""Repositorios SQL (SQLAlchemy Core async, asyncpg) das portas da aplicacao.

Contrato: ``tests/unit/application/test_contrato_portas.py`` (a mesma bateria
roda contra os fakes e contra o Postgres, em ``tests/integration``).

- **Relogio (D5):** nenhum ``now()`` do banco; instantes vem do ``Clock`` da
  unidade de trabalho ou por parametro.
- **Fencing (D4):** ``UPDATE ... WHERE lock_token = :t`` e decisao pelo
  ``rowcount``; ``confirmar_lock`` trava a linha (``FOR UPDATE``).
- **Pega do outbox:** ``FOR UPDATE SKIP LOCKED LIMIT 1`` numa CTE + ``UPDATE``
  com token novo; o recover so reivindica job travado com ``locked_until < agora``.
- **Insercao que pode colidir** roda em SAVEPOINT: a violacao vira
  ``RegistroImutavel``/``ChaveEmUso`` e a transacao da unidade continua valida.
- ``pedido_sysfertil`` vazio vira ``NULL`` (§6); ``request_json``/``response_json``
  so quando o corpo e JSON que o ``jsonb`` aceita (senao ``NULL``); resposta acima
  de 1 MiB e truncada com ``response_truncado``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, NoReturn
from uuid import UUID, uuid4

from sqlalchemy import bindparam, cast, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.types import Text

from app.application.ports import (
    ChaveEmUso,
    Clock,
    ConflitoDeVersao,
    ContratoRegistro,
    EntradaParcelas,
    EnvioRegistrado,
    EventoRegistrado,
    JobPego,
    MensagemSap,
    NovoContrato,
    NovoJob,
    RegistroImutavel,
    ResultadoEnvio,
    SnapshotContrato,
)
from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.states import Ator, Transicao
from app.infrastructure.db.modelos import (
    contract_events,
    contract_snapshots,
    contract_submissions,
    contracts,
    outbox_jobs,
    sap_heartbeat,
)
from app.infrastructure.db.serializacao import (
    contrato_de_json,
    contrato_para_json,
    entrada_para_json,
)

RESPOSTA_MAX_BYTES: Final = 1_048_576  # CHECK response_1mib do schema

# Escapes que o jsonb do Postgres recusa (NUL e surrogate isolado/qualquer).
_ESCAPE_RECUSADO_PELO_JSONB: Final = re.compile(
    r"\\u(?:0000|[dD][89abAB][0-9a-fA-F]{2}|[dD][c-fC-F][0-9a-fA-F]{2})"
)


def _restricao(erro: IntegrityError) -> str | None:
    """Nome da constraint violada (asyncpg: ``constraint_name`` na causa)."""
    for candidato in (getattr(erro.orig, "__cause__", None), erro.orig):
        nome = getattr(candidato, "constraint_name", None)
        if isinstance(nome, str):
            return nome
    return None


def _json_texto_ou_none(corpo: bytes | None) -> str | None:
    """Texto do corpo se ele for JSON que o ``jsonb`` aceita; senao ``None``.

    O cast ``text -> jsonb`` e feito no banco para manter numero exato (sem float).
    """
    if corpo is None:
        return None
    try:
        texto = corpo.decode("utf-8")
        json.loads(texto, parse_constant=_recusar_constante)
    except (UnicodeDecodeError, ValueError):
        return None
    if _ESCAPE_RECUSADO_PELO_JSONB.search(texto):
        return None
    return texto


def _recusar_constante(nome: str) -> NoReturn:
    raise ValueError(f"constante JSON nao suportada pelo jsonb: {nome}")


def _jsonb(texto: str | None) -> Any:
    return cast(bindparam(None, texto, type_=Text), JSONB)


# ---- Contratos ------------------------------------------------------------------------


class RepoContratos:
    def __init__(self, conn: AsyncConnection, relogio: Clock) -> None:
        self._conn = conn
        self._relogio = relogio

    async def inserir(self, novo: NovoContrato) -> None:
        agora = self._relogio.agora()
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    contracts.insert().values(
                        id=novo.id,
                        status=ContractStatus.RASCUNHO.value,
                        origin=novo.origin,
                        created_by=novo.created_by,
                        idempotency_key=novo.idempotency_key,
                        pedido_sysfertil=novo.pedido_sysfertil or None,
                        entrada=entrada_para_json(novo.entrada),
                        sap_contract_number=None,
                        version=1,
                        created_at=agora,
                        updated_at=agora,
                    )
                )
        except IntegrityError as erro:
            _traduzir_unique_de_contrato(erro, novo.id)

    async def obter(self, contract_id: UUID) -> ContratoRegistro | None:
        linha = (
            await self._conn.execute(
                select(
                    contracts.c.id,
                    contracts.c.status,
                    contracts.c.version,
                    contracts.c.sap_contract_number,
                ).where(contracts.c.id == contract_id)
            )
        ).one_or_none()
        if linha is None:
            return None
        return ContratoRegistro(
            id=linha.id,
            status=ContractStatus(linha.status),
            version=linha.version,
            sap_contract_number=linha.sap_contract_number,
        )

    async def atualizar_status(
        self,
        contract_id: UUID,
        *,
        version_esperada: int,
        para: ContractStatus,
        sap_contract_number: str | None = None,
    ) -> ContratoRegistro:
        valores: dict[str, Any] = {
            "status": para.value,
            "version": contracts.c.version + 1,
            "updated_at": self._relogio.agora(),
        }
        if sap_contract_number is not None:
            valores["sap_contract_number"] = sap_contract_number
        try:
            async with self._conn.begin_nested():
                linha = (
                    await self._conn.execute(
                        update(contracts)
                        .where(
                            contracts.c.id == contract_id,
                            contracts.c.version == version_esperada,
                        )
                        .values(**valores)
                        .returning(
                            contracts.c.id,
                            contracts.c.status,
                            contracts.c.version,
                            contracts.c.sap_contract_number,
                        )
                    )
                ).one_or_none()
        except IntegrityError as erro:
            _traduzir_unique_de_contrato(erro, contract_id)
        if linha is None:
            atual = await self.obter(contract_id)
            if atual is None:
                raise LookupError(f"contrato {contract_id} nao existe")
            raise ConflitoDeVersao(f"versao {atual.version}, esperada {version_esperada}")
        return ContratoRegistro(
            id=linha.id,
            status=ContractStatus(linha.status),
            version=linha.version,
            sap_contract_number=linha.sap_contract_number,
        )


def _traduzir_unique_de_contrato(erro: IntegrityError, contract_id: UUID) -> NoReturn:
    restricao = _restricao(erro)
    if restricao == "pk_contracts":
        raise RegistroImutavel(f"contrato {contract_id} ja existe") from erro
    if restricao == "uq_contracts_idempotency_key":
        raise ChaveEmUso("idempotency_key") from erro
    if restricao == "uq_contracts_pedido_sysfertil_ativo":
        raise ChaveEmUso("pedido_sysfertil") from erro
    raise erro


# ---- Snapshots ------------------------------------------------------------------------


def _entrada_parcelas_para_json(e: EntradaParcelas) -> dict[str, Any]:
    return {
        "total": f"{e.total:f}",
        "pesos": list(e.pesos),
        "datas": [d.isoformat() for d in e.datas],
        "form_pag": e.form_pag,
    }


def _entrada_parcelas_de_json(d: dict[str, Any]) -> EntradaParcelas:
    return EntradaParcelas(
        total=Decimal(d["total"]),
        pesos=tuple(int(p) for p in d["pesos"]),
        datas=tuple(date.fromisoformat(x) for x in d["datas"]),
        form_pag=d["form_pag"],
    )


class RepoSnapshots:
    def __init__(self, conn: AsyncConnection, relogio: Clock) -> None:
        self._conn = conn
        self._relogio = relogio

    async def gravar(self, snapshot: SnapshotContrato) -> None:
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    contract_snapshots.insert().values(
                        id=snapshot.id,
                        contract_id=snapshot.contract_id,
                        snapshot=contrato_para_json(snapshot.contrato),
                        algoritmo_parcelas=snapshot.algoritmo_parcelas,
                        entrada_parcelas=_entrada_parcelas_para_json(snapshot.entrada_parcelas),
                        total=snapshot.entrada_parcelas.total,
                        created_at=self._relogio.agora(),
                    )
                )
        except IntegrityError as erro:
            if _restricao(erro) == "pk_contract_snapshots":
                raise RegistroImutavel(f"snapshot {snapshot.id} ja existe") from erro
            raise

    async def obter(self, snapshot_id: UUID) -> SnapshotContrato | None:
        linha = (
            await self._conn.execute(
                select(contract_snapshots).where(contract_snapshots.c.id == snapshot_id)
            )
        ).one_or_none()
        if linha is None:
            return None
        return SnapshotContrato(
            id=linha.id,
            contract_id=linha.contract_id,
            contrato=contrato_de_json(linha.snapshot),
            algoritmo_parcelas=linha.algoritmo_parcelas,
            entrada_parcelas=_entrada_parcelas_de_json(linha.entrada_parcelas),
        )


# ---- Eventos --------------------------------------------------------------------------


class RepoEventos:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def anexar(
        self, contract_id: UUID, transicao: Transicao, *, occurred_at: datetime
    ) -> None:
        await self._conn.execute(
            contract_events.insert().values(
                contract_id=contract_id,
                de=transicao.de.value,
                para=transicao.para.value,
                evento=transicao.evento.value,
                ator_kind=transicao.ator.kind.value,
                ator_identifier=transicao.ator.identifier,
                justificativa=transicao.justificativa,
                sap_contract_number=transicao.sap_contract_number,
                detalhe=dict(transicao.detalhe),
                occurred_at=occurred_at,
            )
        )

    async def listar(self, contract_id: UUID) -> Sequence[EventoRegistrado]:
        linhas = await self._conn.execute(
            select(contract_events)
            .where(contract_events.c.contract_id == contract_id)
            .order_by(contract_events.c.id)
        )
        return tuple(
            EventoRegistrado(
                transicao=Transicao(
                    de=ContractStatus(ln.de),
                    para=ContractStatus(ln.para),
                    evento=TransitionEvent(ln.evento),
                    ator=Ator(ActorKind(ln.ator_kind), ln.ator_identifier),
                    justificativa=ln.justificativa,
                    sap_contract_number=ln.sap_contract_number,
                    detalhe=dict(ln.detalhe),
                ),
                occurred_at=ln.occurred_at,
            )
            for ln in linhas
        )


# ---- Outbox ---------------------------------------------------------------------------

# SQL literal (sem interpolacao): a pega precisa de CTE + UPDATE ... FROM.
_PEGAR_PROXIMO = text(
    """
    WITH alvo AS (
        SELECT id FROM contratos.outbox_jobs
        WHERE concluido_em IS NULL AND lock_token IS NULL AND run_after <= :agora
        ORDER BY run_after, seq
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE contratos.outbox_jobs AS j
    SET lock_token = :token, locked_until = :lock_ate, attempts = j.attempts + 1
    FROM alvo WHERE j.id = alvo.id
    RETURNING j.id, j.contract_id, j.snapshot_id, j.attempts, j.lock_token,
              j.locked_until, j.correlation_id
    """
)

_REIVINDICAR_EXPIRADO = text(
    """
    WITH alvo AS (
        SELECT id FROM contratos.outbox_jobs
        WHERE concluido_em IS NULL AND lock_token IS NOT NULL AND locked_until < :agora
        ORDER BY locked_until, seq
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE contratos.outbox_jobs AS j
    SET lock_token = :token, locked_until = :lock_ate
    FROM alvo WHERE j.id = alvo.id
    RETURNING j.id, j.contract_id, j.snapshot_id, j.attempts, j.lock_token,
              j.locked_until, j.correlation_id
    """
)


def _job_pego(linha: Any) -> JobPego:
    return JobPego(
        id=linha.id,
        contract_id=linha.contract_id,
        snapshot_id=linha.snapshot_id,
        tentativa=linha.attempts,
        lock_token=linha.lock_token,
        locked_until=linha.locked_until,
        correlation_id=linha.correlation_id,
    )


class RepoOutbox:
    def __init__(self, conn: AsyncConnection, relogio: Clock) -> None:
        self._conn = conn
        self._relogio = relogio

    async def enfileirar(self, job: NovoJob) -> None:
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    outbox_jobs.insert().values(
                        id=job.id,
                        contract_id=job.contract_id,
                        snapshot_id=job.snapshot_id,
                        kind="CREATE",
                        run_after=job.run_after,
                        attempts=0,
                        correlation_id=job.correlation_id,
                    )
                )
        except IntegrityError as erro:
            if _restricao(erro) == "pk_outbox_jobs":
                raise RegistroImutavel(f"job {job.id} ja existe") from erro
            raise

    async def _pegar(self, sql: Any, *, agora: datetime, lock_ate: datetime) -> JobPego | None:
        linha = (
            await self._conn.execute(sql, {"agora": agora, "lock_ate": lock_ate, "token": uuid4()})
        ).one_or_none()
        return None if linha is None else _job_pego(linha)

    async def pegar_proximo(self, *, agora: datetime, lock_ate: datetime) -> JobPego | None:
        return await self._pegar(_PEGAR_PROXIMO, agora=agora, lock_ate=lock_ate)

    async def reivindicar_expirado(self, *, agora: datetime, lock_ate: datetime) -> JobPego | None:
        return await self._pegar(_REIVINDICAR_EXPIRADO, agora=agora, lock_ate=lock_ate)

    def _com_token(self, job_id: UUID, lock_token: UUID) -> Any:
        return (
            outbox_jobs.c.id == job_id,
            outbox_jobs.c.lock_token == lock_token,
            outbox_jobs.c.concluido_em.is_(None),
        )

    async def confirmar_lock(self, job_id: UUID, *, lock_token: UUID) -> bool:
        linha = (
            await self._conn.execute(
                select(outbox_jobs.c.id)
                .where(*self._com_token(job_id, lock_token))
                .with_for_update()
            )
        ).one_or_none()
        return linha is not None

    async def reagendar(
        self, job_id: UUID, *, lock_token: UUID, run_after: datetime, ultimo_erro: str
    ) -> bool:
        resultado = await self._conn.execute(
            update(outbox_jobs)
            .where(*self._com_token(job_id, lock_token))
            .values(run_after=run_after, lock_token=None, locked_until=None, last_error=ultimo_erro)
        )
        return resultado.rowcount == 1

    async def concluir(self, job_id: UUID, *, lock_token: UUID) -> bool:
        resultado = await self._conn.execute(
            update(outbox_jobs)
            .where(*self._com_token(job_id, lock_token))
            .values(concluido_em=self._relogio.agora(), lock_token=None, locked_until=None)
        )
        return resultado.rowcount == 1


# ---- Envios (marcador) ------------------------------------------------------------------


def _mensagem_para_json(m: MensagemSap) -> dict[str, str | None]:
    return {"code": m.code, "message": m.message, "target": m.target, "path": m.path}


class RepoEnvios:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def registrar_envio(self, envio: EnvioRegistrado) -> None:
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    contract_submissions.insert().values(
                        id=envio.id,
                        job_id=envio.job_id,
                        contract_id=envio.contract_id,
                        snapshot_id=envio.snapshot_id,
                        tentativa=envio.tentativa,
                        request_sent_at=envio.request_sent_at,
                        request_body=envio.request_body,
                        request_sha256=envio.request_sha256,
                        request_json=_jsonb(_json_texto_ou_none(envio.request_body)),
                        respondido=False,
                    )
                )
        except IntegrityError as erro:
            if _restricao(erro) in {
                "pk_contract_submissions",
                "uq_contract_submissions_job_tentativa",
            }:
                raise RegistroImutavel(f"envio {envio.id} ja registrado") from erro
            raise

    async def existe_para(self, job_id: UUID, *, tentativa: int) -> bool:
        linha = (
            await self._conn.execute(
                select(contract_submissions.c.id).where(
                    contract_submissions.c.job_id == job_id,
                    contract_submissions.c.tentativa == tentativa,
                )
            )
        ).first()
        return linha is not None

    async def obter(self, envio_id: UUID) -> EnvioRegistrado | None:
        linha = (
            await self._conn.execute(
                select(contract_submissions).where(contract_submissions.c.id == envio_id)
            )
        ).one_or_none()
        if linha is None:
            return None
        return EnvioRegistrado(
            id=linha.id,
            job_id=linha.job_id,
            contract_id=linha.contract_id,
            snapshot_id=linha.snapshot_id,
            tentativa=linha.tentativa,
            request_sent_at=linha.request_sent_at,
            request_body=bytes(linha.request_body),
            request_sha256=linha.request_sha256,
        )

    async def registrar_resposta(self, envio_id: UUID, resultado: ResultadoEnvio) -> None:
        respondido = (
            await self._conn.execute(
                select(contract_submissions.c.respondido)
                .where(contract_submissions.c.id == envio_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if respondido is None:
            raise LookupError(f"envio {envio_id} nao existe")
        if respondido:
            raise RegistroImutavel(f"envio {envio_id} ja tem resposta")
        corpo = resultado.response_body
        truncado: bool | None = None
        if corpo is not None:
            truncado = len(corpo) > RESPOSTA_MAX_BYTES
            corpo = corpo[:RESPOSTA_MAX_BYTES]
        await self._conn.execute(
            update(contract_submissions)
            .where(contract_submissions.c.id == envio_id)
            .values(
                response_status=resultado.response_status,
                response_body=corpo,
                response_truncado=truncado,
                response_json=_jsonb(None if truncado else _json_texto_ou_none(corpo)),
                sap_messages=[_mensagem_para_json(m) for m in resultado.mensagens],
                duration_ms=resultado.duracao_ms,
                error_class=resultado.error_class,
                respondido=True,
            )
        )

    async def resposta(self, envio_id: UUID) -> ResultadoEnvio | None:
        linha = (
            await self._conn.execute(
                select(contract_submissions).where(
                    contract_submissions.c.id == envio_id,
                    contract_submissions.c.respondido.is_(True),
                )
            )
        ).one_or_none()
        if linha is None:
            return None
        return ResultadoEnvio(
            response_status=linha.response_status,
            response_body=None if linha.response_body is None else bytes(linha.response_body),
            mensagens=tuple(MensagemSap(**m) for m in (linha.sap_messages or [])),
            duracao_ms=linha.duration_ms,
            error_class=linha.error_class,
        )


# ---- Heartbeat ------------------------------------------------------------------------


class RepoHeartbeat:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def registrar_csrf_ok(self, *, agora: datetime) -> None:
        await self._conn.execute(
            update(sap_heartbeat).where(sap_heartbeat.c.id == 1).values(ultimo_csrf_ok=agora)
        )

    async def ultimo_csrf_ok(self) -> datetime | None:
        valor: datetime | None = (
            await self._conn.execute(
                select(sap_heartbeat.c.ultimo_csrf_ok).where(sap_heartbeat.c.id == 1)
            )
        ).scalar_one_or_none()
        return valor
