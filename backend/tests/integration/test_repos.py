"""Repositorios SQL: o que so o Postgres mostra (Tarefa 2.4).

Round-trip exato de decimais (snapshot em JSONB com string de escala fixa e
``total`` em NUMERIC), ``pedido_sysfertil`` vazio gravado como NULL, rascunho em
JSONB, ``request_json``/``response_json`` so quando o ``jsonb`` aceita e
truncamento da resposta em 1 MiB.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.ports import (
    EntradaParcelas,
    EnvioRegistrado,
    NovoContrato,
    NovoJob,
    ResultadoEnvio,
    SnapshotContrato,
)
from app.domain.contract import Contract
from app.infrastructure.db.modelos import contract_snapshots, contract_submissions, contracts
from app.infrastructure.db.repos import RESPOSTA_MAX_BYTES
from app.infrastructure.db.uow import SqlUnitOfWork
from tests.integration.conftest import RelogioFixo
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _uow(engine: AsyncEngine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine, RelogioFixo(T0))


def _novo(pedido: str | None = None, entrada: dict[str, object] | None = None) -> NovoContrato:
    return NovoContrato(
        id=uuid4(),
        origin="API",
        created_by="n8n",
        idempotency_key=str(uuid4()),
        pedido_sysfertil=pedido,
        entrada=entrada if entrada is not None else {},
    )


def _snapshot(contract_id: object) -> SnapshotContrato:
    return SnapshotContrato(
        id=uuid4(),
        contract_id=contract_id,  # type: ignore[arg-type]
        contrato=Contract.criar(payload_exemplo_como_entrada()),
        algoritmo_parcelas="maior-resto/1",
        entrada_parcelas=EntradaParcelas(
            total=Decimal("23299.55"),
            pesos=(1, 1, 1),
            datas=(date(2026, 9, 4), date(2026, 10, 4), date(2026, 11, 3)),
            form_pag="K",
        ),
    )


async def _job_com_snapshot(engine: AsyncEngine) -> tuple[NovoJob, SnapshotContrato]:
    c = _novo()
    s = _snapshot(c.id)
    job = NovoJob(id=uuid4(), contract_id=c.id, snapshot_id=s.id, run_after=T0, correlation_id="c")
    async with _uow(engine) as uow:
        await uow.contratos.inserir(c)
        await uow.snapshots.gravar(s)
        await uow.outbox.enfileirar(job)
        await uow.commit()
    return job, s


# ---- Contratos ------------------------------------------------------------------------------


async def test_pedido_vazio_e_gravado_como_null_e_timestamps_do_relogio(
    engine: AsyncEngine,
) -> None:
    c = _novo(pedido="")
    async with _uow(engine) as uow:
        await uow.contratos.inserir(c)
        await uow.commit()
    async with engine.connect() as conn:
        linha = (
            await conn.execute(
                select(
                    contracts.c.pedido_sysfertil, contracts.c.created_at, contracts.c.updated_at
                ).where(contracts.c.id == c.id)
            )
        ).one()
    assert linha.pedido_sysfertil is None
    assert (linha.created_at, linha.updated_at) == (T0, T0)


async def test_rascunho_vai_para_jsonb_com_decimal_e_data_como_texto(engine: AsyncEngine) -> None:
    c = _novo(entrada={"total": Decimal("23299.550"), "datas": [date(2026, 9, 4)], "n": 3})
    async with _uow(engine) as uow:
        await uow.contratos.inserir(c)
        await uow.commit()
    async with engine.connect() as conn:
        entrada = (
            await conn.execute(select(contracts.c.entrada).where(contracts.c.id == c.id))
        ).scalar_one()
    assert entrada == {"total": "23299.550", "datas": ["2026-09-04"], "n": 3}


# ---- Snapshot -------------------------------------------------------------------------------


async def test_snapshot_round_trip_exato_e_decimal_como_string_no_jsonb(
    engine: AsyncEngine,
) -> None:
    _, s = await _job_com_snapshot(engine)
    async with _uow(engine) as uow:
        lido = await uow.snapshots.obter(s.id)
    assert lido == s
    assert lido is not None
    assert str(lido.contrato.items[0].pricing[0].condition_rate_value) == "1164.980000000"
    async with engine.connect() as conn:
        linha = (
            await conn.execute(
                select(contract_snapshots.c.snapshot, contract_snapshots.c.total).where(
                    contract_snapshots.c.id == s.id
                )
            )
        ).one()
    assert linha.total == Decimal("23299.55")
    assert linha.snapshot["to_FormPag"][0]["Valor"] == "7766.52"  # string, nunca numero
    assert "StatusBlock" not in linha.snapshot


# ---- Envios: request_json/response_json e truncamento ----------------------------------------


async def _envio(engine: AsyncEngine, corpo: bytes) -> EnvioRegistrado:
    job, _ = await _job_com_snapshot(engine)
    envio = EnvioRegistrado.criar(
        job_id=job.id,
        contract_id=job.contract_id,
        snapshot_id=job.snapshot_id,
        tentativa=1,
        request_sent_at=T0,
        request_body=corpo,
    )
    async with _uow(engine) as uow:
        await uow.envios.registrar_envio(envio)
        await uow.commit()
    return envio


async def _colunas(engine: AsyncEngine, envio_id: object) -> dict[str, object]:
    async with engine.connect() as conn:
        linha = (
            await conn.execute(
                select(contract_submissions).where(contract_submissions.c.id == envio_id)
            )
        ).one()
    return dict(linha._mapping)


async def test_request_json_guarda_o_numero_exato(engine: AsyncEngine) -> None:
    corpo = b'{"Valor":7766.52,"ConditionRateValue":1164.980000000}'
    envio = await _envio(engine, corpo)
    col = await _colunas(engine, envio.id)
    assert bytes(col["request_body"]) == corpo  # type: ignore[arg-type]
    assert col["request_sha256"] == hashlib.sha256(corpo).hexdigest()
    async with engine.connect() as conn:
        texto = (
            await conn.execute(
                text(
                    "SELECT request_json->>'ConditionRateValue' FROM contratos.contract_submissions"
                    " WHERE id = :id"
                ),
                {"id": envio.id},
            )
        ).scalar_one()
    assert texto == "1164.980000000"  # jsonb numeric: sem passar por float


async def test_corpo_que_o_jsonb_recusa_fica_so_em_bytes(engine: AsyncEngine) -> None:
    for corpo in (b"nao-e-json", b'{"x":"\\u0000"}', b'{"x":NaN}', b"\xff\xfe", b'"\\ud800"'):
        envio = await _envio(engine, corpo)
        col = await _colunas(engine, envio.id)
        assert bytes(col["request_body"]) == corpo  # type: ignore[arg-type]
        assert col["request_json"] is None


async def test_resposta_json_e_resposta_grande_truncada(engine: AsyncEngine) -> None:
    envio = await _envio(engine, b"{}")
    async with _uow(engine) as uow:
        await uow.envios.registrar_resposta(
            envio.id,
            ResultadoEnvio(
                response_status=201,
                response_body=b'{"SalesContract":"0040001234"}',
                mensagens=(),
                duracao_ms=10,
                error_class=None,
            ),
        )
        await uow.commit()
    col = await _colunas(engine, envio.id)
    assert col["response_json"] == {"SalesContract": "0040001234"}
    assert col["response_truncado"] is False
    assert col["respondido"] is True

    grande = await _envio(engine, b"{}")
    corpo = b"x" * (RESPOSTA_MAX_BYTES + 10)
    async with _uow(engine) as uow:
        await uow.envios.registrar_resposta(
            grande.id,
            ResultadoEnvio(
                response_status=500,
                response_body=corpo,
                mensagens=(),
                duracao_ms=10,
                error_class="SAP_5XX",
            ),
        )
        await uow.commit()
    col = await _colunas(engine, grande.id)
    assert len(bytes(col["response_body"])) == RESPOSTA_MAX_BYTES  # type: ignore[arg-type]
    assert col["response_truncado"] is True
    assert col["response_json"] is None
