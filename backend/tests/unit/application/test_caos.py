"""Caos (D13): excecao em cada ponto entre o commit do marcador e o do resultado.

Para cada ponto (e com o fallback tambem falhando ou nao), o recover roda depois
do lock expirar. Asserts: o contrato NUNCA volta a ``NA_FILA`` depois do
``WORKER_PEGOU``, o job NUNCA e reagendado, o estado final e ``INCERTO`` e o
alerta sai exatamente uma vez. Vale ate para desfechos que sozinhos iriam para
``CRIADO`` ou para retry (``FALHA_ANTES_POST``): com excecao no meio, na duvida,
``INCERTO``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.application.ports import DesfechoPost
from app.application.process_outbox_job import PontoCaos
from app.domain.enums import ContractStatus, TransitionEvent
from app.domain.states import MATRIZ
from tests.unit.application._cenario import LOCK, Cenario, desfecho

S = ContractStatus
E = TransitionEvent

_PONTOS = [p for p in PontoCaos if p is not PontoCaos.NO_FALLBACK]
_DESFECHOS = {
    "201": desfecho(E.SAP_201, status=201, numero="1"),
    "retry": desfecho(E.FALHA_ANTES_POST),
    "timeout": desfecho(E.TIMEOUT_APOS_POST),
    "negocio": desfecho(E.SAP_4XX_NEGOCIO, status=400),
}


class Caos(Exception):
    pass


def test_todos_os_pontos_estao_cobertos() -> None:
    assert {p.value for p in _PONTOS} == {
        "antes_do_post",
        "depois_do_post",
        "antes_de_gravar_resposta",
        "antes_da_transicao",
        "antes_do_evento",
        "antes_do_job",
        "antes_do_commit",
    }


@pytest.mark.parametrize("fallback_falha", [False, True], ids=["fallback", "recover"])
@pytest.mark.parametrize("nome", list(_DESFECHOS))
@pytest.mark.parametrize("ponto", _PONTOS, ids=lambda p: p.value)
async def test_excecao_depois_do_marcador_termina_em_incerto(
    ponto: PontoCaos, nome: str, *, fallback_falha: bool
) -> None:
    c = Cenario()
    cid = await c.rascunho()
    r = await c.submeter(cid)
    d: DesfechoPost = _DESFECHOS[nome]
    c.gateway.post = [d]

    async def gancho(p: PontoCaos) -> None:
        if p is ponto or (fallback_falha and p is PontoCaos.NO_FALLBACK):
            raise Caos(p.value)

    await c.processador(gancho=gancho).processar_proximo()
    c.relogio.avancar(LOCK + timedelta(seconds=1))
    while await c.recuperar() is not None:
        pass

    assert await c.status(cid) is S.INCERTO
    eventos = await c.eventos(cid)
    assert eventos[:2] == [E.SUBMETER, E.WORKER_PEGOU]
    assert len(eventos) == 3
    assert eventos[2] is (
        E.LOCK_EXPIRADO_COM_ENVIO if fallback_falha else E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO
    )
    if not fallback_falha:
        async with c.uow() as uow:
            ultimo = (await uow.eventos.listar(cid))[-1].transicao
        assert ultimo.detalhe == {"fase": "pos_envio", "error_class": "Caos", "tentativa": 1}
    for ev in eventos[2:]:
        assert MATRIZ[(S.ENVIANDO, ev)].para is not S.NA_FILA
    job = c.banco.estado.jobs[r.job_id]
    assert job.concluido
    assert job.ultimo_erro is None  # nunca reagendado
    assert job.tentativas == 1
    assert [n for n, _ in c.metricas.chamadas] == ["contrato_incerto"]
    assert len(c.banco.estado.envios) == 1
    # so o POST do ponto ANTES_DO_POST nao chegou a sair; o marcador existe sempre
    assert len(c.gateway.corpos) == (0 if ponto is PontoCaos.ANTES_DO_POST else 1)
