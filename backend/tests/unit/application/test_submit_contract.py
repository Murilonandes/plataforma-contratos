"""Caso de uso ``submeter_contrato`` (D1, D6'). Tarefa 2.8."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest

from app.application.ports import ChaveEmUso
from app.application.snapshot import Algoritmo, montar_contrato
from app.application.submit_contract import (
    ContratoNaoEncontrado,
    PedidoSubmissao,
    submeter_contrato,
)
from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.errors import DomainValidationError, InvalidTransitionError
from app.domain.installments import ALGORITMO_PARCELAS, ParcelaCalculada, calcular_parcelas
from app.domain.states import Ator
from tests.unit.application._cenario import (
    PARCELAS,
    T0,
    VENDEDOR,
    Cenario,
    entrada_sem_parcelas,
)

S = ContractStatus


async def test_rascunho_vai_para_a_fila_com_snapshot_evento_e_job_na_mesma_transacao() -> None:
    c = Cenario()
    cid = await c.rascunho()
    r = await c.submeter(cid)

    async with c.uow() as uow:
        registro = await uow.contratos.obter(cid)
        snapshot = await uow.snapshots.obter(r.snapshot_id)
        eventos = await uow.eventos.listar(cid)
    assert registro is not None
    assert (registro.status, registro.version) == (S.NA_FILA, 2)
    assert snapshot is not None
    assert snapshot.contract_id == cid
    assert snapshot.algoritmo_parcelas == ALGORITMO_PARCELAS
    assert snapshot.entrada_parcelas == PARCELAS
    assert snapshot.contrato == montar_contrato(entrada_sem_parcelas(), PARCELAS)
    assert [(e.transicao.evento, e.transicao.ator, e.occurred_at) for e in eventos] == [
        (TransitionEvent.SUBMETER, VENDEDOR, T0)
    ]
    assert r.transicao == eventos[0].transicao
    (job,) = c.banco.estado.jobs.values()
    assert (job.novo.id, job.novo.contract_id, job.novo.snapshot_id) == (
        r.job_id,
        cid,
        r.snapshot_id,
    )
    assert (job.novo.run_after, job.novo.correlation_id) == (T0, "corr-1")


async def test_nova_submissao_depois_de_erro_negocio_gera_snapshot_novo() -> None:
    c = Cenario()
    cid = await c.rascunho()
    primeira = await c.submeter(cid)
    await c.forcar_status(cid, S.ERRO_NEGOCIO)
    segunda = await c.submeter(cid)
    assert await c.status(cid) is S.NA_FILA
    assert segunda.snapshot_id != primeira.snapshot_id
    assert len(c.banco.estado.snapshots) == 2


async def _nada_gravado(c: Cenario, cid: object, status: ContractStatus) -> None:
    assert await c.status(cid) is status  # type: ignore[arg-type]
    assert c.banco.estado.snapshots == {}
    assert c.banco.estado.jobs == {}
    assert c.banco.estado.eventos.get(cid, []) == []  # type: ignore[call-overload]


async def test_contrato_invalido_nao_grava_nada() -> None:
    c = Cenario()
    cid = await c.rascunho()
    entrada = entrada_sem_parcelas()
    entrada["SoldToParty"] = ""
    with pytest.raises(DomainValidationError) as exc:
        await c.submeter(cid, entrada=entrada)
    assert [e.path for e in exc.value.errors] == ["SoldToParty"]
    await _nada_gravado(c, cid, S.RASCUNHO)


async def test_to_formpag_do_cliente_e_recusado_sem_gravar() -> None:
    c = Cenario()
    cid = await c.rascunho()
    entrada = entrada_sem_parcelas()
    entrada["to_FormPag"] = []
    with pytest.raises(ValueError) as exc:  # noqa: PT011 — mensagem conferida abaixo
        await c.submeter(cid, entrada=entrada)
    assert str(exc.value) == (
        "to_FormPag nunca vem do cliente: as parcelas sao calculadas no servidor"
    )
    await _nada_gravado(c, cid, S.RASCUNHO)


@pytest.mark.parametrize("status", [S.NA_FILA, S.ENVIANDO, S.CRIADO, S.INCERTO, S.CANCELADO])
async def test_estado_que_nao_submete_e_transicao_invalida(status: ContractStatus) -> None:
    c = Cenario()
    cid = await c.rascunho()
    await c.forcar_status(cid, status)
    with pytest.raises(InvalidTransitionError):
        await c.submeter(cid)
    await _nada_gravado(c, cid, status)


async def test_contrato_inexistente() -> None:
    c = Cenario()
    cid = await c.rascunho()
    outro = type(cid)(int=cid.int ^ 1)
    with pytest.raises(ContratoNaoEncontrado) as exc:
        await c.submeter(outro)
    assert str(exc.value) == f"contrato {outro} nao encontrado"
    assert exc.value.contract_id == outro


async def test_ator_que_nao_e_usuario_e_recusado_pela_transition() -> None:
    c = Cenario()
    cid = await c.rascunho()
    with pytest.raises(DomainValidationError) as exc:
        await submeter_contrato(
            PedidoSubmissao(
                contract_id=cid,
                entrada=entrada_sem_parcelas(),
                parcelas=PARCELAS,
                ator=Ator(ActorKind.WORKER, "w1"),
                correlation_id="c",
            ),
            nova_uow=c.uow,
            relogio=c.relogio,
        )
    assert [e.path for e in exc.value.errors] == ["ator.kind"]
    await _nada_gravado(c, cid, S.RASCUNHO)


async def test_reativacao_que_colide_no_pedido_propaga_chave_em_uso_sem_gravar() -> None:
    c = Cenario()
    antigo = await c.rascunho(pedido="PG285")
    await c.forcar_status(antigo, S.ERRO_NEGOCIO)
    await c.rascunho(pedido="PG285")  # outro contrato ativo com o mesmo pedido
    with pytest.raises(ChaveEmUso):
        await c.submeter(antigo)
    assert await c.status(antigo) is S.ERRO_NEGOCIO
    assert c.banco.estado.snapshots == {}
    assert c.banco.estado.jobs == {}


async def test_justificativa_do_vendedor_vai_para_o_evento() -> None:
    c = Cenario()
    cid = await c.rascunho()
    r = await submeter_contrato(
        PedidoSubmissao(
            contract_id=cid,
            entrada=entrada_sem_parcelas(),
            parcelas=PARCELAS,
            ator=VENDEDOR,
            correlation_id="c",
            justificativa="  cliente pediu urgencia  ",
        ),
        nova_uow=c.uow,
        relogio=c.relogio,
    )
    assert r.transicao.justificativa == "cliente pediu urgencia"


def _invertido(
    total: Decimal, pesos: Sequence[int], datas: Sequence[date]
) -> tuple[ParcelaCalculada, ...]:
    ps = calcular_parcelas(total, pesos, datas)
    return tuple(
        dataclasses.replace(p, valor=q.valor) for p, q in zip(ps, reversed(ps), strict=True)
    )


async def test_algoritmo_injetado_calcula_e_carimba_o_snapshot() -> None:
    c = Cenario()
    cid = await c.rascunho()
    r = await submeter_contrato(
        PedidoSubmissao(
            contract_id=cid,
            entrada=entrada_sem_parcelas(),
            parcelas=PARCELAS,
            ator=VENDEDOR,
            correlation_id="c",
        ),
        nova_uow=c.uow,
        relogio=c.relogio,
        algoritmo=Algoritmo("teste/1", _invertido),
    )
    snapshot = c.banco.estado.snapshots[r.snapshot_id]
    assert snapshot.algoritmo_parcelas == "teste/1"
    assert [p.valor for p in snapshot.contrato.installments] == [
        Decimal("7766.51"),
        Decimal("7766.52"),
        Decimal("7766.52"),
    ]
