"""Caso de uso ``recover_expired_locks`` (§4, D4). Tarefa 2.8."""

from __future__ import annotations

from datetime import timedelta

from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.states import Ator
from tests.unit.application._cenario import LOCK, T0, Cenario

S = ContractStatus
E = TransitionEvent
_DEPOIS = LOCK + timedelta(seconds=1)


async def _preso_em_enviando(c: Cenario, *, com_marcador: bool) -> tuple[object, object]:
    """Worker pegou o job e morreu (antes ou depois de gravar o marcador)."""
    cid = await c.rascunho()
    r = await c.submeter(cid)
    p = c.processador()
    pego = await p._pegar()  # commit 1: WORKER_PEGOU
    assert pego is not None
    job, _, snapshot, _ = pego
    if com_marcador:
        assert snapshot is not None
        assert await p._marcar(job, snapshot, b"{}", token_novo=False) is not None  # commit 2
    return cid, r.job_id


async def test_sem_job_expirado_devolve_none() -> None:
    c = Cenario()
    await _preso_em_enviando(c, com_marcador=False)
    assert await c.recuperar() is None  # lock ainda valido


async def test_sem_marcador_volta_para_a_fila_e_e_pego_de_novo() -> None:
    c = Cenario()
    cid, job_id = await _preso_em_enviando(c, com_marcador=False)
    c.relogio.avancar(_DEPOIS)
    r = await c.recuperar()
    assert r is not None
    assert (r.evento, r.para) == (E.LOCK_EXPIRADO_SEM_ENVIO, S.NA_FILA)
    assert await c.status(cid) is S.NA_FILA  # type: ignore[arg-type]
    job = c.banco.estado.jobs[job_id]  # type: ignore[index]
    assert (job.concluido, job.lock_token, job.novo.run_after) == (False, None, T0 + _DEPOIS)
    assert job.ultimo_erro == "LOCK_EXPIRADO_SEM_ENVIO"
    async with c.uow() as uow:
        ultimo = (await uow.eventos.listar(cid))[-1]  # type: ignore[arg-type]
    assert ultimo.transicao.ator == Ator(ActorKind.SYSTEM, "recover")
    assert ultimo.transicao.detalhe == {"tentativa": 1}
    assert ultimo.occurred_at == T0 + _DEPOIS
    assert c.metricas.chamadas == []
    pego = await c.processador()._pegar()
    assert pego is not None
    assert pego[0].tentativa == 2


async def test_com_marcador_vai_para_incerto_e_conclui_o_job() -> None:
    c = Cenario()
    cid, job_id = await _preso_em_enviando(c, com_marcador=True)
    c.relogio.avancar(_DEPOIS)
    r = await c.recuperar()
    assert r is not None
    assert (r.evento, r.para) == (E.LOCK_EXPIRADO_COM_ENVIO, S.INCERTO)
    assert c.banco.estado.jobs[job_id].concluido  # type: ignore[index]
    assert c.metricas.chamadas == [
        ("contrato_incerto", {"transicao": "LOCK_EXPIRADO_COM_ENVIO", "contract_id": str(cid)})
    ]
    assert await c.recuperar() is None


async def test_job_expirado_de_contrato_fora_de_enviando_e_descartado() -> None:
    c = Cenario()
    cid, job_id = await _preso_em_enviando(c, com_marcador=True)
    await c.forcar_status(cid, S.CANCELADO)  # type: ignore[arg-type]
    c.relogio.avancar(_DEPOIS)
    r = await c.recuperar()
    assert r is not None
    assert (r.evento, r.para) == (None, None)
    assert c.banco.estado.jobs[job_id].concluido  # type: ignore[index]
    assert await c.eventos(cid) == [E.SUBMETER, E.WORKER_PEGOU]  # type: ignore[arg-type]
    assert c.metricas.chamadas == []
