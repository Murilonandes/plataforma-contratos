"""Schema ``contratos`` no PostgreSQL 16 real (Tarefa 2.3).

Migracao (upgrade -> downgrade -> upgrade + ``alembic check``), tipos (TEXT +
CHECK, nunca ENUM; TIMESTAMPTZ; NUMERIC), unique parcial de ``pedido_sysfertil``,
concorrencia de dois workers com ``FOR UPDATE SKIP LOCKED``, fencing por
``UPDATE ... WHERE lock_token = :t`` + ``rowcount`` e imutabilidade por trigger.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tests.integration.conftest import rodar_alembic

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


# ---- Ajudantes SQL ------------------------------------------------------------------


async def _contrato(
    c: AsyncConnection, *, status: str = "RASCUNHO", pedido: str | None = "PG285"
) -> UUID:
    cid = uuid4()
    await c.execute(
        text(
            "INSERT INTO contratos.contracts (id, status, origin, created_by, idempotency_key,"
            " pedido_sysfertil, entrada, version, created_at, updated_at) VALUES (:id, :st,"
            " 'WEB', 'oid', :k, :p, '{}'::jsonb, 1, :t, :t)"
        ),
        {"id": cid, "st": status, "k": str(uuid4()), "p": pedido, "t": T0},
    )
    return cid


async def _snapshot(c: AsyncConnection, contract_id: UUID) -> UUID:
    sid = uuid4()
    await c.execute(
        text(
            "INSERT INTO contratos.contract_snapshots (id, contract_id, snapshot,"
            " algoritmo_parcelas, entrada_parcelas, total, created_at) VALUES (:id, :c,"
            " '{}'::jsonb, 'maior-resto/1', '{}'::jsonb, 23299.55, :t)"
        ),
        {"id": sid, "c": contract_id, "t": T0},
    )
    return sid


async def _job(c: AsyncConnection, *, run_after: datetime = T0) -> UUID:
    cid = await _contrato(c, pedido=None)
    sid = await _snapshot(c, cid)
    jid = uuid4()
    await c.execute(
        text(
            "INSERT INTO contratos.outbox_jobs (id, contract_id, snapshot_id, kind, run_after,"
            " attempts, correlation_id) VALUES (:id, :c, :s, 'CREATE', :r, 0, 'corr')"
        ),
        {"id": jid, "c": cid, "s": sid, "r": run_after},
    )
    return jid


async def _envio(c: AsyncConnection, job_id: UUID, corpo: bytes = b'{"a":1}') -> UUID:
    row = (
        await c.execute(
            text("SELECT contract_id, snapshot_id FROM contratos.outbox_jobs WHERE id = :j"),
            {"j": job_id},
        )
    ).one()
    eid = uuid4()
    await c.execute(
        text(
            "INSERT INTO contratos.contract_submissions (id, job_id, contract_id, snapshot_id,"
            " tentativa, request_sent_at, request_body, request_sha256, respondido) VALUES"
            " (:id, :j, :c, :s, 1, :t, :b, :h, false)"
        ),
        {
            "id": eid,
            "j": job_id,
            "c": row.contract_id,
            "s": row.snapshot_id,
            "t": T0,
            "b": corpo,
            "h": hashlib.sha256(corpo).hexdigest(),
        },
    )
    return eid


_PEGAR = text(
    "SELECT id FROM contratos.outbox_jobs"
    " WHERE concluido_em IS NULL AND lock_token IS NULL AND run_after <= :agora"
    " ORDER BY run_after, seq LIMIT 1 FOR UPDATE SKIP LOCKED"
)


def _mensagem(exc: pytest.ExceptionInfo[DBAPIError]) -> str:
    return str(exc.value.orig)


# ---- Migracao -----------------------------------------------------------------------


async def test_upgrade_downgrade_upgrade_e_check(url_banco: str) -> None:
    await rodar_alembic(url_banco, "downgrade", "base")
    await rodar_alembic(url_banco, "upgrade", "head")
    await rodar_alembic(url_banco, "check")  # levanta se modelo e migracao divergirem


async def test_downgrade_base_nao_deixa_nada_alem_da_tabela_de_versao(
    url_banco: str, engine: AsyncEngine
) -> None:
    await rodar_alembic(url_banco, "downgrade", "base")
    async with engine.connect() as c:
        tabelas = (
            await c.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'contratos'"
                )
            )
        ).scalars()
        funcoes = (
            await c.execute(
                text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                    " WHERE n.nspname = 'contratos'"
                )
            )
        ).scalar_one()
    assert set(tabelas) == {"alembic_version"}
    assert funcoes == 0


# ---- Tipos --------------------------------------------------------------------------


async def test_tudo_no_schema_contratos_nada_no_public(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        public = (
            await c.execute(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
            )
        ).scalar_one()
        nossas = set(
            (
                await c.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = 'contratos'"
                    )
                )
            ).scalars()
        )
    assert public == 0
    assert nossas == {
        "alembic_version",
        "contracts",
        "contract_snapshots",
        "contract_events",
        "outbox_jobs",
        "contract_submissions",
        "sap_heartbeat",
    }


async def test_instantes_timestamptz_dinheiro_numeric_estado_text_sem_enum_nem_float(
    engine: AsyncEngine,
) -> None:
    async with engine.connect() as c:
        colunas = (
            await c.execute(
                text(
                    "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale"
                    " FROM information_schema.columns WHERE table_schema = 'contratos'"
                )
            )
        ).all()
        enums = (
            await c.execute(
                text(
                    "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace"
                    " WHERE n.nspname = 'contratos' AND t.typtype = 'e'"
                )
            )
        ).scalar_one()
    tipos = {(t, col): (dt, p, s) for t, col, dt, p, s in colunas}
    assert enums == 0
    assert not [k for k, (dt, _, _) in tipos.items() if dt == "timestamp without time zone"]
    assert not [k for k, (dt, _, _) in tipos.items() if dt in {"real", "double precision"}]
    assert tipos[("contract_snapshots", "total")] == ("numeric", 15, 2)
    for tabela, coluna in [
        ("contracts", "status"),
        ("contract_events", "de"),
        ("contract_events", "para"),
        ("contract_events", "evento"),
        ("contract_events", "ator_kind"),
    ]:
        assert tipos[(tabela, coluna)][0] == "text"
    for tabela, coluna in [
        ("contracts", "created_at"),
        ("outbox_jobs", "run_after"),
        ("outbox_jobs", "locked_until"),
        ("contract_submissions", "request_sent_at"),
        ("contract_events", "occurred_at"),
    ]:
        assert tipos[(tabela, coluna)][0] == "timestamp with time zone"
    assert tipos[("contract_submissions", "request_body")][0] == "bytea"
    assert tipos[("outbox_jobs", "lock_token")][0] == "uuid"


@pytest.mark.parametrize(
    ("sql", "constraint"),
    [
        ("UPDATE contratos.contracts SET status = 'APROVADO'", "ck_contracts_status"),
        (
            "UPDATE contratos.contracts SET pedido_sysfertil = ''",
            "ck_contracts_pedido_sysfertil_nao_vazio",
        ),
        (
            "UPDATE contratos.contracts SET sap_contract_number = '40001234'",
            "ck_contracts_sap_contract_number_vbeln",
        ),
        ("UPDATE contratos.contracts SET origin = 'N8N'", "ck_contracts_origin"),
        ("UPDATE contratos.contracts SET version = 0", "ck_contracts_version"),
    ],
)
async def test_check_constraints_de_contracts(
    engine: AsyncEngine, sql: str, constraint: str
) -> None:
    async with engine.begin() as c:
        await _contrato(c)
    with pytest.raises(IntegrityError) as exc:
        async with engine.begin() as c:
            await c.execute(text(sql))
    assert constraint in _mensagem(exc)


async def test_numero_sap_canonico_e_aceito(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        cid = await _contrato(c)
        await c.execute(
            text("UPDATE contratos.contracts SET sap_contract_number = '0040001234' WHERE id = :i"),
            {"i": cid},
        )


async def test_evento_fora_do_enum_e_recusado(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        cid = await _contrato(c)
    with pytest.raises(IntegrityError) as exc:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO contratos.contract_events (contract_id, de, para, evento,"
                    " ator_kind, ator_identifier, detalhe, occurred_at) VALUES (:c, 'RASCUNHO',"
                    " 'NA_FILA', 'EVENTO_INVENTADO', 'user', 'oid', '{}'::jsonb, :t)"
                ),
                {"c": cid, "t": T0},
            )
    assert "ck_contract_events_evento" in _mensagem(exc)


async def test_heartbeat_tem_uma_linha_so(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        assert (
            await c.execute(text("SELECT id FROM contratos.sap_heartbeat"))
        ).scalars().all() == [1]
    with pytest.raises(IntegrityError) as exc:
        async with engine.begin() as c:
            await c.execute(text("INSERT INTO contratos.sap_heartbeat (id) VALUES (2)"))
    assert "ck_sap_heartbeat_linha_unica" in _mensagem(exc)


# ---- Unique parcial de pedido_sysfertil ------------------------------------------------


async def test_pedido_sysfertil_unico_entre_contratos_ativos(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        await _contrato(c, pedido="PG1")
    with pytest.raises(IntegrityError) as exc:
        async with engine.begin() as c:
            await _contrato(c, pedido="PG1")
    assert "uq_contracts_pedido_sysfertil_ativo" in _mensagem(exc)


@pytest.mark.parametrize("liberado", ["ERRO_NEGOCIO", "CANCELADO"])
async def test_pedido_liberado_quando_o_anterior_nao_esta_ativo(
    engine: AsyncEngine, liberado: str
) -> None:
    async with engine.begin() as c:
        await _contrato(c, status=liberado, pedido="PG2")
        await _contrato(c, pedido="PG2")  # ok: o anterior nao conta
        await _contrato(c, status=liberado, pedido="PG2")  # varios inativos tambem


async def test_pedido_nulo_nao_colide(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        for _ in range(3):
            await _contrato(c, pedido=None)


async def test_reativar_contrato_com_pedido_repetido_e_barrado(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        antigo = await _contrato(c, status="ERRO_NEGOCIO", pedido="PG3")
        await _contrato(c, pedido="PG3")
    with pytest.raises(IntegrityError):
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE contratos.contracts SET status = 'NA_FILA' WHERE id = :i"),
                {"i": antigo},
            )


# ---- Outbox: SKIP LOCKED com dois workers e fencing ---------------------------------------


async def test_dois_workers_concorrentes_nunca_pegam_o_mesmo_job(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        j1 = await _job(c, run_after=T0 - timedelta(seconds=2))
        j2 = await _job(c, run_after=T0 - timedelta(seconds=1))
    async with engine.connect() as w1, engine.connect() as w2:
        await w1.begin()
        await w2.begin()
        pego1 = (await w1.execute(_PEGAR, {"agora": T0})).scalar_one_or_none()
        pego2 = (await w2.execute(_PEGAR, {"agora": T0})).scalar_one_or_none()  # nao espera w1
        pego2_de_novo = (await w2.execute(_PEGAR, {"agora": T0})).scalar_one_or_none()
        await w1.rollback()
        await w2.rollback()
    assert (pego1, pego2) == (j1, j2)
    assert pego2_de_novo == j2  # a mesma transacao reve a linha que ja travou


async def test_um_job_so_com_dois_workers_o_segundo_nao_espera_e_nao_pega(
    engine: AsyncEngine,
) -> None:
    async with engine.begin() as c:
        j = await _job(c)
    async with engine.connect() as w1, engine.connect() as w2:
        await w1.begin()
        await w2.begin()
        assert (await w1.execute(_PEGAR, {"agora": T0})).scalar_one_or_none() == j
        assert (await w2.execute(_PEGAR, {"agora": T0})).scalar_one_or_none() is None
        await w1.rollback()
        await w2.rollback()


async def test_run_after_comparado_com_o_agora_do_parametro(engine: AsyncEngine) -> None:
    """D5: o predicado usa o horario do Clock (parametro), nunca now() do banco."""
    async with engine.begin() as c:
        j = await _job(c, run_after=T0)
        antes = (await c.execute(_PEGAR, {"agora": T0 - timedelta(seconds=1)})).scalar_one_or_none()
        agora = (await c.execute(_PEGAR, {"agora": T0})).scalar_one_or_none()
    assert (antes, agora) == (None, j)


async def test_fencing_update_com_token_errado_nao_grava(engine: AsyncEngine) -> None:
    token, outro = uuid4(), uuid4()
    async with engine.begin() as c:
        j = await _job(c)
        await c.execute(
            text(
                "UPDATE contratos.outbox_jobs SET lock_token = :t, locked_until = :u,"
                " attempts = attempts + 1 WHERE id = :j"
            ),
            {"t": token, "u": T0 + timedelta(minutes=10), "j": j},
        )
    concluir = text(
        "UPDATE contratos.outbox_jobs SET concluido_em = :agora, lock_token = NULL,"
        " locked_until = NULL WHERE id = :j AND lock_token = :t AND concluido_em IS NULL"
    )
    async with engine.begin() as c:
        errado = await c.execute(concluir, {"agora": T0, "j": j, "t": outro})
        certo = await c.execute(concluir, {"agora": T0, "j": j, "t": token})
        de_novo = await c.execute(concluir, {"agora": T0, "j": j, "t": token})
    assert (errado.rowcount, certo.rowcount, de_novo.rowcount) == (0, 1, 0)


@pytest.mark.parametrize(
    ("sql", "constraint"),
    [
        (
            "UPDATE contratos.outbox_jobs SET lock_token = gen_random_uuid()",
            "ck_outbox_jobs_lock_par",
        ),
        (
            "UPDATE contratos.outbox_jobs SET locked_until = now()",
            "ck_outbox_jobs_lock_par",
        ),
        (
            "UPDATE contratos.outbox_jobs SET concluido_em = now(), lock_token = gen_random_uuid(),"
            " locked_until = now()",
            "ck_outbox_jobs_concluido_sem_lock",
        ),
        ("UPDATE contratos.outbox_jobs SET kind = 'CHANGE_STATUS'", "ck_outbox_jobs_kind"),
        ("UPDATE contratos.outbox_jobs SET attempts = -1", "ck_outbox_jobs_attempts"),
    ],
)
async def test_check_constraints_do_outbox(engine: AsyncEngine, sql: str, constraint: str) -> None:
    async with engine.begin() as c:
        await _job(c)
    with pytest.raises(IntegrityError) as exc:
        async with engine.begin() as c:
            await c.execute(text(sql))
    assert constraint in _mensagem(exc)


# ---- Imutabilidade por trigger ----------------------------------------------------------


@pytest.mark.parametrize("tabela", ["contract_events", "contract_snapshots"])
@pytest.mark.parametrize("operacao", ["UPDATE", "DELETE", "TRUNCATE"])
async def test_eventos_e_snapshots_so_aceitam_insert(
    engine: AsyncEngine, tabela: str, operacao: str
) -> None:
    async with engine.begin() as c:
        cid = await _contrato(c)
        await _snapshot(c, cid)
        await c.execute(
            text(
                "INSERT INTO contratos.contract_events (contract_id, de, para, evento, ator_kind,"
                " ator_identifier, detalhe, occurred_at) VALUES (:c, 'RASCUNHO', 'NA_FILA',"
                " 'SUBMETER', 'user', 'oid', '{}'::jsonb, :t)"
            ),
            {"c": cid, "t": T0},
        )
    # tabela vem do parametrize (lista fixa acima), nunca de entrada externa
    sql = {
        "UPDATE": f"UPDATE contratos.{tabela} SET contract_id = contract_id",  # noqa: S608
        "DELETE": f"DELETE FROM contratos.{tabela}",  # noqa: S608
        "TRUNCATE": f"TRUNCATE contratos.{tabela} CASCADE",
    }[operacao]
    with pytest.raises(DBAPIError) as exc:
        async with engine.begin() as c:
            await c.execute(text(sql))
    assert f"{tabela}: registro imutavel ({operacao} proibido)" in _mensagem(exc)


async def _responder(c: AsyncConnection, envio: UUID, **extra: Any) -> None:
    campos = {"status": 201, "body": b"{}", "json": json.dumps({}), **extra}
    await c.execute(
        text(
            "UPDATE contratos.contract_submissions SET response_status = :status,"
            " response_body = :body, response_json = CAST(:json AS jsonb), respondido = true,"
            " duration_ms = 10 WHERE id = :id"
        ),
        {"id": envio, **campos},
    )


async def test_submissao_aceita_uma_resposta_so(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        envio = await _envio(c, await _job(c))
    async with engine.begin() as c:
        await _responder(c, envio)
    with pytest.raises(DBAPIError) as exc:
        async with engine.begin() as c:
            await _responder(c, envio, status=500)
    assert "contract_submissions: resposta ja gravada (imutavel)" in _mensagem(exc)


async def test_submissao_nao_muda_os_campos_do_envio(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        envio = await _envio(c, await _job(c))
    corpo = b'{"outro":1}'
    with pytest.raises(DBAPIError) as exc:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE contratos.contract_submissions SET request_body = :b,"
                    " request_sha256 = :h, respondido = true WHERE id = :id"
                ),
                {"b": corpo, "h": hashlib.sha256(corpo).hexdigest(), "id": envio},
            )
    assert "contract_submissions: campos do envio sao imutaveis" in _mensagem(exc)


async def test_submissao_update_sem_resposta_e_recusado(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        envio = await _envio(c, await _job(c))
    with pytest.raises(DBAPIError) as exc:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE contratos.contract_submissions SET error_class = 'x' WHERE id = :id"),
                {"id": envio},
            )
    assert "contract_submissions: UPDATE so para gravar a resposta" in _mensagem(exc)


@pytest.mark.parametrize(
    ("sql", "mensagem"),
    [
        ("DELETE FROM contratos.contract_submissions", "contract_submissions: DELETE proibido"),
        (
            "TRUNCATE contratos.contract_submissions",
            "contract_submissions: registro imutavel (TRUNCATE proibido)",
        ),
    ],
)
async def test_submissao_nao_e_apagada(engine: AsyncEngine, sql: str, mensagem: str) -> None:
    async with engine.begin() as c:
        await _envio(c, await _job(c))
    with pytest.raises(DBAPIError) as exc:
        async with engine.begin() as c:
            await c.execute(text(sql))
    assert mensagem in _mensagem(exc)


async def test_sha256_do_envio_conferido_pelo_banco(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        j = await _job(c)
        row = (
            await c.execute(
                text("SELECT contract_id, snapshot_id FROM contratos.outbox_jobs WHERE id = :j"),
                {"j": j},
            )
        ).one()
    with pytest.raises(IntegrityError) as exc:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO contratos.contract_submissions (id, job_id, contract_id,"
                    " snapshot_id, tentativa, request_sent_at, request_body, request_sha256,"
                    " respondido) VALUES (:id, :j, :c, :s, 1, :t, :b, :h, false)"
                ),
                {
                    "id": uuid4(),
                    "j": j,
                    "c": row.contract_id,
                    "s": row.snapshot_id,
                    "t": T0,
                    "b": b"{}",
                    "h": "0" * 64,
                },
            )
    assert "ck_contract_submissions_request_sha256" in _mensagem(exc)


async def test_um_marcador_por_job_e_tentativa(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        j = await _job(c)
        await _envio(c, j)
    with pytest.raises(IntegrityError) as exc:
        async with engine.begin() as c:
            await _envio(c, j)
    assert "uq_contract_submissions_job_tentativa" in _mensagem(exc)
