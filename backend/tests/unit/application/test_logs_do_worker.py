"""Logs do ``process_outbox_job`` e do ``recover_expired_locks`` (Tarefa 2.8).

Cada linha de log tem campos exatos: so ids, evento e classe da excecao; nunca
corpo, header ou mensagem de excecao (que pode carregar dado do SAP).
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from structlog.testing import capture_logs

from app.application.ports import DesfechoCsrf, NovoJob
from app.application.process_outbox_job import PontoCaos
from app.application.snapshot import Algoritmo
from app.domain.enums import ContractStatus, TransitionEvent
from app.domain.installments import calcular_parcelas
from tests.unit.application._cenario import LOCK, T0, Cenario, desfecho
from tests.unit.application.test_process_outbox_job import _calculo_que_diverge

S = ContractStatus
E = TransitionEvent
_DEPOIS = LOCK + timedelta(seconds=1)

Logs = list[MutableMapping[str, Any]]


@pytest.fixture
def logs() -> Iterator[Logs]:
    with capture_logs() as capturados:
        yield capturados


def _de(logs: Logs, evento: str) -> Logs:
    return [dict(x) for x in logs if x["event"] == evento]


async def _na_fila(c: Cenario) -> tuple[UUID, UUID]:
    cid = await c.rascunho()
    r = await c.submeter(cid)
    return cid, r.job_id


def _ctx(cid: UUID, job_id: UUID, tentativa: int = 1) -> dict[str, Any]:
    return {
        "correlation_id": "corr-1",
        "contract_id": str(cid),
        "job_id": str(job_id),
        "tentativa": tentativa,
    }


def test_pontos_de_caos_tem_valores_fixos() -> None:
    assert [p.value for p in PontoCaos] == [
        "antes_do_post",
        "depois_do_post",
        "antes_de_gravar_resposta",
        "antes_da_transicao",
        "antes_do_evento",
        "antes_do_job",
        "antes_do_commit",
        "no_fallback",
    ]


async def test_job_de_contrato_fora_da_fila(logs: Logs) -> None:
    c = Cenario()
    cid, job_id = await _na_fila(c)
    await c.forcar_status(cid, S.CANCELADO)
    await c.processador().processar_proximo()
    assert _de(logs, "job_descartado") == [
        {
            "event": "job_descartado",
            "log_level": "warning",
            "job_id": str(job_id),
            "contract_id": str(cid),
            "status": "CANCELADO",
        }
    ]


async def test_job_de_contrato_inexistente(logs: Logs) -> None:
    c = Cenario()
    cid, job_id = await _na_fila(c)
    fantasma = uuid4()
    c.banco.estado.jobs[job_id].novo = NovoJob(
        id=job_id, contract_id=fantasma, snapshot_id=uuid4(), run_after=T0, correlation_id="x"
    )
    r = await c.processador().processar_proximo()
    assert r is not None
    assert r.evento is None
    assert _de(logs, "job_descartado") == [
        {
            "event": "job_descartado",
            "log_level": "warning",
            "job_id": str(job_id),
            "contract_id": str(fantasma),
            "status": None,
        }
    ]
    assert await c.status(cid) is S.NA_FILA


async def test_versao_pulada(logs: Logs) -> None:
    c = Cenario()
    cid, _ = await _na_fila(c)
    c.gateway.post = [desfecho(E.SAP_201, status=201, numero="1")]
    await c.processador(algoritmo=Algoritmo("v9", calcular_parcelas)).processar_proximo()
    assert _de(logs, "conferencia_pulada_versao") == [
        {
            "event": "conferencia_pulada_versao",
            "log_level": "warning",
            "contract_id": str(cid),
            "algoritmo_snapshot": "maior-resto/1",
            "algoritmo_atual": "v9",
        }
    ]


async def test_mesma_versao_nao_loga_pulada(logs: Logs) -> None:
    c = Cenario()
    await _na_fila(c)
    c.gateway.post = [desfecho(E.SAP_201, status=201, numero="1")]
    await c.processador().processar_proximo()
    assert _de(logs, "conferencia_pulada_versao") == []
    assert [x["event"] for x in logs] == []  # caminho feliz: o worker nao loga nada


async def test_preparo_e_conferencia(logs: Logs) -> None:
    c = Cenario()
    cid, job_id = await _na_fila(c)

    def explode(_: object) -> bytes:
        raise ValueError("segredo do corpo")

    await c.processador(corpo_de=explode).processar_proximo()
    assert _de(logs, "preparo_falhou") == [
        {
            "event": "preparo_falhou",
            "log_level": "error",
            "error_class": "ValueError",
            **_ctx(cid, job_id),
        }
    ]
    assert "segredo" not in repr(logs)

    c2 = Cenario()
    cid2, job2 = await _na_fila(c2)
    p = c2.processador(algoritmo=Algoritmo("maior-resto/1", _calculo_que_diverge))
    await p.processar_proximo()
    assert _de(logs, "conferencia_divergente") == [
        {
            "event": "conferencia_divergente",
            "log_level": "error",
            "parcela": 1,
            "campo": "Valor",
            **_ctx(cid2, job2),
        }
    ]


async def test_lock_perdido_antes_do_marcador(logs: Logs) -> None:
    c = Cenario()
    cid, job_id = await _na_fila(c)
    original = c.gateway.preparar

    async def rouba(**kw: Any) -> DesfechoCsrf:
        c.relogio.avancar(_DEPOIS)
        assert await c.recuperar() is not None
        return await original(**kw)

    c.gateway.preparar = rouba  # type: ignore[method-assign]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert r.evento is None
    assert c.banco.estado.envios == {}
    assert c.gateway.corpos == []
    assert _de(logs, "lock_perdido") == [
        {"event": "lock_perdido", "log_level": "warning", "fase": "marcador", **_ctx(cid, job_id)}
    ]


async def test_lock_perdido_no_desfecho_sem_envio(logs: Logs) -> None:
    c = Cenario()
    cid, job_id = await _na_fila(c)

    async def rouba(**_: Any) -> DesfechoCsrf:
        c.relogio.avancar(_DEPOIS)
        assert await c.recuperar() is not None
        return DesfechoCsrf(E.FALHA_ANTES_POST, {"fase": "csrf", "status": 500})

    c.gateway.preparar = rouba  # type: ignore[method-assign]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert r.evento is None
    assert await c.eventos(cid) == [E.SUBMETER, E.WORKER_PEGOU, E.LOCK_EXPIRADO_SEM_ENVIO]
    assert _de(logs, "lock_perdido") == [
        {
            "event": "lock_perdido",
            "log_level": "warning",
            "fase": "sem_envio",
            "job_id": str(job_id),
        }
    ]


async def test_lock_perdido_no_resultado(logs: Logs) -> None:
    c = Cenario()
    _, job_id = await _na_fila(c)
    c.gateway.post = [desfecho(E.SAP_201, status=201, numero="1")]

    async def gancho(p: PontoCaos) -> None:
        if p is PontoCaos.DEPOIS_DO_POST:
            c.relogio.avancar(_DEPOIS)
            await c.recuperar()

    await c.processador(gancho=gancho).processar_proximo()
    assert _de(logs, "lock_perdido") == [
        {
            "event": "lock_perdido",
            "log_level": "warning",
            "fase": "resultado",
            "job_id": str(job_id),
        }
    ]


async def test_falha_depois_do_marcador_e_fallback(logs: Logs) -> None:
    c = Cenario()
    cid, job_id = await _na_fila(c)
    c.gateway.post = [desfecho(E.SAP_201, status=201, numero="1")]

    async def gancho(p: PontoCaos) -> None:
        if p in (PontoCaos.ANTES_DO_COMMIT, PontoCaos.NO_FALLBACK):
            raise KeyError("segredo")

    await c.processador(gancho=gancho).processar_proximo()
    assert _de(logs, "falha_depois_do_marcador") == [
        {
            "event": "falha_depois_do_marcador",
            "log_level": "error",
            "error_class": "KeyError",
            **_ctx(cid, job_id),
        }
    ]
    assert _de(logs, "fallback_incerto_falhou") == [
        {
            "event": "fallback_incerto_falhou",
            "log_level": "error",
            "contract_id": str(cid),
            "job_id": str(job_id),
            "error_class": "KeyError",
        }
    ]
    assert "segredo" not in repr(logs)


async def test_fallback_com_lock_perdido_nao_grava(logs: Logs) -> None:
    c = Cenario()
    cid, _ = await _na_fila(c)
    c.gateway.post = [desfecho(E.SAP_201, status=201, numero="1")]

    async def gancho(p: PontoCaos) -> None:
        if p is PontoCaos.ANTES_DO_COMMIT:
            raise RuntimeError("x")
        if p is PontoCaos.NO_FALLBACK:
            c.relogio.avancar(_DEPOIS)
            await c.recuperar()

    r = await c.processador(gancho=gancho).processar_proximo()
    assert r is not None
    assert r.evento is None
    assert await c.eventos(cid) == [E.SUBMETER, E.WORKER_PEGOU, E.LOCK_EXPIRADO_COM_ENVIO]
    assert [n for n, _ in c.metricas.chamadas] == ["contrato_incerto"]


# ---- recover -----------------------------------------------------------------------------------


async def test_logs_do_recover(logs: Logs) -> None:
    c = Cenario()
    cid, job_id = await _na_fila(c)
    assert await c.processador()._pegar() is not None
    c.relogio.avancar(_DEPOIS)
    await c.recuperar()
    assert _de(logs, "lock_expirado_recuperado") == [
        {
            "event": "lock_expirado_recuperado",
            "log_level": "warning",
            "contract_id": str(cid),
            "job_id": str(job_id),
            "evento": "LOCK_EXPIRADO_SEM_ENVIO",
        }
    ]

    c2 = Cenario()
    cid2, job2 = await _na_fila(c2)
    assert await c2.processador()._pegar() is not None
    await c2.forcar_status(cid2, S.CANCELADO)
    c2.relogio.avancar(_DEPOIS)
    await c2.recuperar()
    assert _de(logs, "recover_job_descartado") == [
        {
            "event": "recover_job_descartado",
            "log_level": "warning",
            "job_id": str(job2),
            "contract_id": str(cid2),
            "status": "CANCELADO",
        }
    ]


async def test_recover_de_job_de_contrato_inexistente(logs: Logs) -> None:
    c = Cenario()
    cid, job_id = await _na_fila(c)
    assert await c.processador()._pegar() is not None
    fantasma = uuid4()
    job = c.banco.estado.jobs[job_id]
    job.novo = NovoJob(
        id=job_id,
        contract_id=fantasma,
        snapshot_id=job.novo.snapshot_id,
        run_after=T0,
        correlation_id="x",
    )
    c.relogio.avancar(_DEPOIS)
    r = await c.recuperar()
    assert r is not None
    assert r.evento is None
    assert _de(logs, "recover_job_descartado")[0]["status"] is None
    assert await c.status(cid) is S.ENVIANDO
