"""Contrato comportamental das portas de persistencia.

Toda implementacao de ``UnitOfWork`` precisa passar nesta bateria. Aqui ela roda
contra os fakes em memoria (``fakes.py``); na Tarefa 2.4 a mesma bateria roda
contra o Postgres (testcontainers), trocando so a fixture ``nova_uow``.

Cobre o que os casos de uso assumem: commit/rollback, versao otimista, snapshot
e evento imutaveis, pega do outbox com ``lock_token`` novo, fencing (``confirmar_lock``,
``reagendar`` e ``concluir`` so com o token atual), recuperacao de lock expirado
com rotacao do token, marcador de envio com bytes + sha256 e heartbeat do CSRF.
Todo instante vem por parametro (D5).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.application.ports import (
    ConflitoDeVersao,
    EntradaParcelas,
    EnvioRegistrado,
    MensagemSap,
    NovoContrato,
    NovoJob,
    RegistroImutavel,
    ResultadoEnvio,
    SnapshotContrato,
    UnitOfWork,
)
from app.domain.contract import Contract
from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.states import Ator, transition
from tests.unit.application.fakes import BancoEmMemoria, FakeUnitOfWork
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
LOCK = timedelta(seconds=600)

NovaUow = Callable[[], UnitOfWork]


@pytest.fixture
def nova_uow() -> NovaUow:
    banco = BancoEmMemoria()
    return lambda: FakeUnitOfWork(banco)


def _novo_contrato(pedido: str | None = "PG285") -> NovoContrato:
    return NovoContrato(
        id=uuid4(),
        origin="WEB",
        created_by="oid-vendedor",
        idempotency_key=str(uuid4()),
        pedido_sysfertil=pedido,
        entrada={"SalesOrganization": "BRF1"},
    )


def _snapshot(contract_id: UUID) -> SnapshotContrato:
    return SnapshotContrato(
        id=uuid4(),
        contract_id=contract_id,
        contrato=Contract.criar(payload_exemplo_como_entrada()),
        algoritmo_parcelas="maior-resto/1",
        entrada_parcelas=EntradaParcelas(
            total=Decimal("23299.55"),
            pesos=(1, 1, 1),
            datas=(date(2026, 9, 4), date(2026, 10, 4), date(2026, 11, 3)),
            form_pag="K",
        ),
    )


async def _preparar_job(nova_uow: NovaUow, *, run_after: datetime = T0) -> NovoJob:
    c = _novo_contrato()
    s = _snapshot(c.id)
    job = NovoJob(
        id=uuid4(), contract_id=c.id, snapshot_id=s.id, run_after=run_after, correlation_id="corr-1"
    )
    async with nova_uow() as uow:
        await uow.contratos.inserir(c)
        await uow.snapshots.gravar(s)
        await uow.outbox.enfileirar(job)
        await uow.commit()
    return job


# ---- Unit of Work -------------------------------------------------------------------


async def test_commit_persiste_e_sem_commit_descarta(nova_uow: NovaUow) -> None:
    c1, c2 = _novo_contrato("A1"), _novo_contrato("A2")
    async with nova_uow() as uow:
        await uow.contratos.inserir(c1)
        await uow.commit()
    async with nova_uow() as uow:
        await uow.contratos.inserir(c2)  # sem commit
    async with nova_uow() as uow:
        assert await uow.contratos.obter(c1.id) is not None
        assert await uow.contratos.obter(c2.id) is None


async def test_excecao_dentro_da_uow_descarta_tudo(nova_uow: NovaUow) -> None:
    c = _novo_contrato()

    async def inserir_e_cair() -> None:
        async with nova_uow() as uow:
            await uow.contratos.inserir(c)
            raise RuntimeError("caiu")

    with pytest.raises(RuntimeError, match=r"^caiu$"):
        await inserir_e_cair()
    async with nova_uow() as uow:
        assert await uow.contratos.obter(c.id) is None


# ---- Contratos: versao otimista ----------------------------------------------------------


async def test_contrato_nasce_rascunho_versao_1(nova_uow: NovaUow) -> None:
    c = _novo_contrato()
    async with nova_uow() as uow:
        await uow.contratos.inserir(c)
        reg = await uow.contratos.obter(c.id)
    assert reg is not None
    assert (reg.status, reg.version, reg.sap_contract_number) == (ContractStatus.RASCUNHO, 1, None)


async def test_atualizar_status_incrementa_versao(nova_uow: NovaUow) -> None:
    c = _novo_contrato()
    async with nova_uow() as uow:
        await uow.contratos.inserir(c)
        novo = await uow.contratos.atualizar_status(
            c.id, version_esperada=1, para=ContractStatus.NA_FILA
        )
        await uow.commit()
    assert (novo.status, novo.version) == (ContractStatus.NA_FILA, 2)


async def test_atualizar_status_com_versao_velha_e_conflito(nova_uow: NovaUow) -> None:
    c = _novo_contrato()
    async with nova_uow() as uow:
        await uow.contratos.inserir(c)
        await uow.contratos.atualizar_status(c.id, version_esperada=1, para=ContractStatus.NA_FILA)
        with pytest.raises(ConflitoDeVersao):
            await uow.contratos.atualizar_status(
                c.id, version_esperada=1, para=ContractStatus.CANCELADO
            )


async def test_numero_sap_gravado_na_transicao(nova_uow: NovaUow) -> None:
    c = _novo_contrato()
    async with nova_uow() as uow:
        await uow.contratos.inserir(c)
        reg = await uow.contratos.atualizar_status(
            c.id, version_esperada=1, para=ContractStatus.CRIADO, sap_contract_number="0040001234"
        )
    assert reg.sap_contract_number == "0040001234"


# ---- Snapshot e eventos: imutaveis ---------------------------------------------------------


async def test_snapshot_volta_igual_e_nao_pode_ser_regravado(nova_uow: NovaUow) -> None:
    c = _novo_contrato()
    s = _snapshot(c.id)
    async with nova_uow() as uow:
        await uow.contratos.inserir(c)
        await uow.snapshots.gravar(s)
        await uow.commit()
    async with nova_uow() as uow:
        assert await uow.snapshots.obter(s.id) == s
        with pytest.raises(RegistroImutavel):
            await uow.snapshots.gravar(s)


async def test_eventos_em_ordem_de_anexacao(nova_uow: NovaUow) -> None:
    c = _novo_contrato()
    ator = Ator(ActorKind.USER, "oid-vendedor")
    t1 = transition(ContractStatus.RASCUNHO, TransitionEvent.SUBMETER, ator=ator)
    t2 = transition(
        ContractStatus.NA_FILA,
        TransitionEvent.WORKER_PEGOU,
        ator=Ator(ActorKind.WORKER, "w-1"),
    )
    async with nova_uow() as uow:
        await uow.contratos.inserir(c)
        await uow.eventos.anexar(c.id, t1, occurred_at=T0)
        await uow.eventos.anexar(c.id, t2, occurred_at=T0 + timedelta(seconds=1))
        await uow.commit()
    async with nova_uow() as uow:
        eventos = await uow.eventos.listar(c.id)
    assert [(e.transicao, e.occurred_at) for e in eventos] == [
        (t1, T0),
        (t2, T0 + timedelta(seconds=1)),
    ]


# ---- Outbox: pega, fencing, recuperacao ------------------------------------------------------


async def test_pega_job_vencido_com_lock_token_novo(nova_uow: NovaUow) -> None:
    job = await _preparar_job(nova_uow)
    async with nova_uow() as uow:
        pego = await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK)
        await uow.commit()
    assert pego is not None
    assert (pego.id, pego.contract_id, pego.snapshot_id, pego.tentativa) == (
        job.id,
        job.contract_id,
        job.snapshot_id,
        1,
    )
    assert pego.locked_until == T0 + LOCK
    assert pego.correlation_id == "corr-1"


async def test_job_travado_nao_e_pego_de_novo(nova_uow: NovaUow) -> None:
    await _preparar_job(nova_uow)
    async with nova_uow() as uow:
        assert await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK) is not None
        await uow.commit()
    async with nova_uow() as uow:
        # nem depois do lock expirar: job travado vencido e do recover, nao do pegar_proximo
        assert await uow.outbox.pegar_proximo(agora=T0 + 2 * LOCK, lock_ate=T0 + 3 * LOCK) is None


async def test_job_futuro_nao_e_pego(nova_uow: NovaUow) -> None:
    await _preparar_job(nova_uow, run_after=T0 + timedelta(seconds=30))
    async with nova_uow() as uow:
        assert await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK) is None
        assert (
            await uow.outbox.pegar_proximo(agora=T0 + timedelta(seconds=30), lock_ate=T0 + LOCK)
            is not None
        )


async def test_ordem_de_pega_e_por_run_after(nova_uow: NovaUow) -> None:
    tarde = await _preparar_job(nova_uow, run_after=T0)
    cedo = await _preparar_job(nova_uow, run_after=T0 - timedelta(minutes=5))
    async with nova_uow() as uow:
        primeiro = await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK)
        segundo = await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK)
    assert primeiro is not None
    assert segundo is not None
    assert (primeiro.id, segundo.id) == (cedo.id, tarde.id)


async def test_fencing_so_o_token_atual_confirma_reagenda_e_conclui(nova_uow: NovaUow) -> None:
    await _preparar_job(nova_uow)
    async with nova_uow() as uow:
        pego = await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK)
        await uow.commit()
    assert pego is not None
    outro = uuid4()
    async with nova_uow() as uow:
        assert await uow.outbox.confirmar_lock(pego.id, lock_token=pego.lock_token)
        assert not await uow.outbox.confirmar_lock(pego.id, lock_token=outro)
        assert not await uow.outbox.reagendar(
            pego.id, lock_token=outro, run_after=T0, ultimo_erro="x"
        )
        assert not await uow.outbox.concluir(pego.id, lock_token=outro)
        assert await uow.outbox.concluir(pego.id, lock_token=pego.lock_token)
        await uow.commit()
    async with nova_uow() as uow:
        # concluido: nem o token antigo confirma mais, e o job nao volta
        assert not await uow.outbox.confirmar_lock(pego.id, lock_token=pego.lock_token)
        assert await uow.outbox.pegar_proximo(agora=T0 + LOCK, lock_ate=T0 + 2 * LOCK) is None


async def test_reagendar_solta_o_lock_e_a_proxima_pega_e_outra_tentativa(
    nova_uow: NovaUow,
) -> None:
    await _preparar_job(nova_uow)
    async with nova_uow() as uow:
        p1 = await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK)
        assert p1 is not None
        depois = T0 + timedelta(seconds=30)
        assert await uow.outbox.reagendar(
            p1.id, lock_token=p1.lock_token, run_after=depois, ultimo_erro="ConnectError"
        )
        await uow.commit()
    async with nova_uow() as uow:
        assert await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK) is None
        p2 = await uow.outbox.pegar_proximo(agora=depois, lock_ate=depois + LOCK)
    assert p2 is not None
    assert p2.tentativa == 2
    assert p2.lock_token != p1.lock_token
    assert not await _confirma(nova_uow, p1.id, p1.lock_token)


async def _confirma(nova_uow: NovaUow, job_id: UUID, token: UUID) -> bool:
    async with nova_uow() as uow:
        return await uow.outbox.confirmar_lock(job_id, lock_token=token)


async def test_recover_reivindica_lock_expirado_e_rotaciona_o_token(nova_uow: NovaUow) -> None:
    await _preparar_job(nova_uow)
    async with nova_uow() as uow:
        pego = await uow.outbox.pegar_proximo(agora=T0, lock_ate=T0 + LOCK)
        await uow.commit()
    assert pego is not None
    async with nova_uow() as uow:
        # ainda no prazo: nada a reivindicar
        assert (
            await uow.outbox.reivindicar_expirado(agora=T0 + LOCK, lock_ate=T0 + 2 * LOCK) is None
        )
    depois = T0 + LOCK + timedelta(seconds=1)
    async with nova_uow() as uow:
        rec = await uow.outbox.reivindicar_expirado(agora=depois, lock_ate=depois + LOCK)
        await uow.commit()
    assert rec is not None
    assert (rec.id, rec.tentativa) == (pego.id, pego.tentativa)  # mesma tentativa
    assert rec.lock_token != pego.lock_token
    # fencing: o worker antigo perdeu o direito de gravar; o recover tem o novo
    assert not await _confirma(nova_uow, pego.id, pego.lock_token)
    assert await _confirma(nova_uow, rec.id, rec.lock_token)


async def test_job_sem_lock_nao_e_reivindicado(nova_uow: NovaUow) -> None:
    await _preparar_job(nova_uow)
    async with nova_uow() as uow:
        assert (
            await uow.outbox.reivindicar_expirado(agora=T0 + 9 * LOCK, lock_ate=T0 + 10 * LOCK)
            is None
        )


# ---- Envios (marcador) -------------------------------------------------------------------------


async def test_marcador_de_envio_guarda_bytes_e_hash_e_responde_uma_vez(
    nova_uow: NovaUow,
) -> None:
    job = await _preparar_job(nova_uow)
    corpo = b'{"StatusBlock":"06"}'
    envio = EnvioRegistrado.criar(
        job_id=job.id,
        contract_id=job.contract_id,
        snapshot_id=job.snapshot_id,
        tentativa=1,
        request_sent_at=T0,
        request_body=corpo,
    )
    async with nova_uow() as uow:
        assert not await uow.envios.existe_para(job.id, tentativa=1)
        await uow.envios.registrar_envio(envio)
        await uow.commit()
    async with nova_uow() as uow:
        assert await uow.envios.existe_para(job.id, tentativa=1)
        assert not await uow.envios.existe_para(job.id, tentativa=2)
        lido = await uow.envios.obter(envio.id)
        assert lido is not None
        assert (lido.request_body, lido.request_sha256) == (
            corpo,
            hashlib.sha256(corpo).hexdigest(),
        )
        resultado = ResultadoEnvio(
            response_status=201,
            response_body=b"nao-e-json",
            mensagens=(MensagemSap(code="W1", message="aviso", target=None, path=None),),
            duracao_ms=120,
            error_class=None,
        )
        await uow.envios.registrar_resposta(envio.id, resultado)
        await uow.commit()
    async with nova_uow() as uow:
        assert await uow.envios.resposta(envio.id) == resultado
        with pytest.raises(RegistroImutavel):
            await uow.envios.registrar_resposta(envio.id, resultado)


async def test_mesmo_marcador_nao_pode_ser_registrado_duas_vezes(nova_uow: NovaUow) -> None:
    job = await _preparar_job(nova_uow)
    envio = EnvioRegistrado.criar(
        job_id=job.id,
        contract_id=job.contract_id,
        snapshot_id=job.snapshot_id,
        tentativa=1,
        request_sent_at=T0,
        request_body=b"{}",
    )
    async with nova_uow() as uow:
        await uow.envios.registrar_envio(envio)
        with pytest.raises(RegistroImutavel):
            await uow.envios.registrar_envio(envio)


# ---- Heartbeat do CSRF (saude do SAP fora do ready) ----------------------------------------------


async def test_heartbeat_guarda_o_ultimo_csrf_ok(nova_uow: NovaUow) -> None:
    async with nova_uow() as uow:
        assert await uow.heartbeat.ultimo_csrf_ok() is None
        await uow.heartbeat.registrar_csrf_ok(agora=T0)
        await uow.heartbeat.registrar_csrf_ok(agora=T0 + timedelta(minutes=1))
        await uow.commit()
    async with nova_uow() as uow:
        assert await uow.heartbeat.ultimo_csrf_ok() == T0 + timedelta(minutes=1)
