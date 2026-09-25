"""Caso de uso ``process_outbox_job`` com fakes (D2, D3, D11, D12, D15). Tarefa 2.8."""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from app.application.ports import DesfechoCsrf, MensagemSap, NovoJob
from app.application.process_outbox_job import ConfigWorker, PontoCaos, atraso_retry, nome_do_alerta
from app.application.snapshot import Algoritmo
from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.installments import ALGORITMO_PARCELAS, ParcelaCalculada, calcular_parcelas
from app.domain.states import MATRIZ, Ator, transition
from tests.unit.application._cenario import LOCK, T0, Cenario, corpo_sap, desfecho

S = ContractStatus
E = TransitionEvent
W1 = Ator(ActorKind.WORKER, "w1")


async def _na_fila(c: Cenario) -> tuple[UUID, UUID, UUID]:
    cid = await c.rascunho()
    r = await c.submeter(cid)
    return cid, r.job_id, r.snapshot_id


def _job(c: Cenario, job_id: UUID):  # type: ignore[no-untyped-def]
    return c.banco.estado.jobs[job_id]


def _envios(c: Cenario) -> list:  # type: ignore[type-arg]
    return list(c.banco.estado.envios.values())


async def test_sem_job_devolve_none() -> None:
    c = Cenario()
    assert await c.processador().processar_proximo() is None


async def test_201_ponta_a_ponta() -> None:
    c = Cenario()
    cid, job_id, snapshot_id = await _na_fila(c)
    msgs = (MensagemSap(code="A", message="aviso", target=None, path=None),)
    c.gateway.post = [
        desfecho(
            E.SAP_201,
            status=201,
            corpo=b'{"SalesContract":"40001234"}',
            numero="0040001234",
            mensagens=msgs,
            detalhe={"fase": "post", "status": 201},
        )
    ]
    r = await c.processador().processar_proximo()

    assert r is not None
    assert (r.evento, r.para) == (E.SAP_201, S.CRIADO)
    async with c.uow() as uow:
        registro = await uow.contratos.obter(cid)
        eventos = await uow.eventos.listar(cid)
        snapshot = await uow.snapshots.obter(snapshot_id)
    assert registro is not None
    assert (registro.status, registro.sap_contract_number) == (S.CRIADO, "0040001234")
    assert [(e.transicao.evento, e.transicao.ator, e.occurred_at) for e in eventos] == [
        (E.SUBMETER, eventos[0].transicao.ator, T0),
        (E.WORKER_PEGOU, W1, T0),
        (E.SAP_201, W1, T0),
    ]
    assert eventos[1].transicao.detalhe == {"tentativa": 1}
    assert eventos[2].transicao.detalhe == {"fase": "post", "status": 201, "tentativa": 1}

    assert snapshot is not None
    esperado = corpo_sap(snapshot.contrato)
    assert c.gateway.corpos == [esperado]
    (envio,) = _envios(c)
    assert (envio.job_id, envio.snapshot_id, envio.tentativa) == (job_id, snapshot_id, 1)
    assert envio.request_body == esperado
    assert envio.request_sha256 == hashlib.sha256(esperado).hexdigest()
    assert envio.request_sent_at == T0
    resposta = c.banco.estado.respostas[envio.id]
    assert (resposta.response_status, resposta.response_body) == (
        201,
        b'{"SalesContract":"40001234"}',
    )
    assert (resposta.mensagens, resposta.duracao_ms, resposta.error_class) == (msgs, 12, None)
    assert c.banco.estado.heartbeat_csrf == T0
    assert _job(c, job_id).concluido
    assert c.metricas.chamadas == []


# ---- Cada desfecho do POST, ponta a ponta ------------------------------------------------------

_POST = [
    (desfecho(E.SAP_201, status=201, numero="1"), S.CRIADO, []),
    (desfecho(E.SAP_4XX_NEGOCIO, status=400), S.ERRO_NEGOCIO, []),
    (desfecho(E.SAP_4XX_TECNICO, status=404), S.ERRO_TECNICO, ["erro_tecnico"]),
    (desfecho(E.TIMEOUT_APOS_POST), S.INCERTO, ["contrato_incerto"]),
    (desfecho(E.CONEXAO_CAIDA_APOS_POST), S.INCERTO, ["contrato_incerto"]),
    (desfecho(E.SAP_5XX_APOS_POST, status=503), S.INCERTO, ["contrato_incerto"]),
    (desfecho(E.FALHA_APOS_RESPOSTA, status=302), S.INCERTO, ["contrato_incerto"]),
    (desfecho(E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO), S.INCERTO, ["contrato_incerto"]),
]


@pytest.mark.parametrize(("d", "para", "alertas"), _POST, ids=lambda x: getattr(x, "evento", x))
async def test_desfecho_do_post(d: object, para: ContractStatus, alertas: list[str]) -> None:
    c = Cenario()
    cid, job_id, _ = await _na_fila(c)
    c.gateway.post = [d]  # type: ignore[list-item]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert r.para is para
    assert await c.status(cid) is para
    assert _job(c, job_id).concluido  # corpo enviado: nunca reagenda
    assert _job(c, job_id).ultimo_erro is None
    assert [n for n, _ in c.metricas.chamadas] == alertas
    assert c.metricas.chamadas == [
        (n, {"transicao": d.evento.value, "contract_id": str(cid)})  # type: ignore[attr-defined]
        for n in alertas
    ]
    (envio,) = _envios(c)
    assert envio.id in c.banco.estado.respostas


async def test_connect_error_depois_do_marcador_ainda_e_falha_antes_post_com_retry() -> None:
    c = Cenario()
    _, job_id, _ = await _na_fila(c)
    c.gateway.post = [desfecho(E.FALHA_ANTES_POST, detalhe={"fase": "post", "error_class": "X"})]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert r.para is S.NA_FILA
    job = _job(c, job_id)
    assert not job.concluido
    assert job.lock_token is None
    assert job.novo.run_after == T0 + timedelta(seconds=30)
    assert job.ultimo_erro == "FALHA_ANTES_POST error_class=X fase=post tentativa=1"
    assert c.metricas.chamadas == []
    resposta = c.banco.estado.respostas[_envios(c)[0].id]
    assert (resposta.response_status, resposta.error_class) == (None, "X")


# ---- CSRF (antes do marcador) ------------------------------------------------------------------


async def test_csrf_falha_antes_post_reagenda_sem_marcador_e_sem_post() -> None:
    c = Cenario()
    cid, job_id, _ = await _na_fila(c)
    c.gateway.csrf = [DesfechoCsrf(E.FALHA_ANTES_POST, {"fase": "csrf", "status": 500})]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert r.para is S.NA_FILA
    assert c.gateway.corpos == []
    assert _envios(c) == []
    assert c.banco.estado.heartbeat_csrf is None
    job = _job(c, job_id)
    assert job.novo.run_after == T0 + timedelta(seconds=30)
    assert job.ultimo_erro == "FALHA_ANTES_POST fase=csrf status=500 tentativa=1"
    assert await c.eventos(cid) == [E.SUBMETER, E.WORKER_PEGOU, E.FALHA_ANTES_POST]


@pytest.mark.parametrize("evento", [E.SAP_4XX_TECNICO, E.FALHA_NAO_CLASSIFICADA_ANTES_ENVIO])
async def test_csrf_sem_retry_vai_para_erro_tecnico(evento: TransitionEvent) -> None:
    c = Cenario()
    _, job_id, _ = await _na_fila(c)
    c.gateway.csrf = [DesfechoCsrf(evento, {"fase": "csrf", "status": 401})]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (evento, S.ERRO_TECNICO)
    assert _job(c, job_id).concluido
    assert c.gateway.corpos == []
    assert [n for n, _ in c.metricas.chamadas] == ["erro_tecnico"]


async def test_gateway_que_levanta_no_csrf_e_falha_antes_post() -> None:
    c = Cenario()
    cid, _, _ = await _na_fila(c)

    async def explode(**_: object) -> DesfechoCsrf:
        raise RuntimeError("bug")

    c.gateway.preparar = explode  # type: ignore[method-assign]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert r.para is S.NA_FILA
    async with c.uow() as uow:
        ultimo = (await uow.eventos.listar(cid))[-1].transicao
    assert ultimo.detalhe == {"fase": "csrf", "error_class": "RuntimeError", "tentativa": 1}


async def test_tentativas_esgotadas_vao_para_erro_tecnico() -> None:
    c = Cenario()
    cid, job_id, _ = await _na_fila(c)
    falha = DesfechoCsrf(E.FALHA_ANTES_POST, {"fase": "csrf", "status": 500})
    c.gateway.csrf = [falha, falha]
    p = c.processador(max_tentativas=2)
    primeira = await p.processar_proximo()
    assert primeira is not None
    assert primeira.para is S.NA_FILA
    assert await p.processar_proximo() is None  # backoff: ainda nao
    c.relogio.avancar(timedelta(seconds=30))
    segunda = await p.processar_proximo()
    assert segunda is not None
    assert segunda.job.tentativa == 2
    assert (segunda.evento, segunda.para) == (E.FALHA_ANTES_POST_ESGOTOU, S.ERRO_TECNICO)
    assert _job(c, job_id).concluido
    assert [n for n, _ in c.metricas.chamadas] == ["erro_tecnico"]
    assert await c.eventos(cid) == [
        E.SUBMETER,
        E.WORKER_PEGOU,
        E.FALHA_ANTES_POST,
        E.WORKER_PEGOU,
        E.FALHA_ANTES_POST_ESGOTOU,
    ]


# ---- Backoff (D3) ------------------------------------------------------------------------------

_CFG = ConfigWorker(worker_id="w", lock_timeout=LOCK, max_tentativas=5)


@pytest.mark.parametrize(
    ("tentativa", "sorteio", "segundos"),
    [
        (1, 0.5, 30),
        (2, 0.5, 60),
        (3, 0.5, 120),
        (6, 0.5, 960),
        (7, 0.5, 1800),  # 1920 -> teto
        (10_000, 0.5, 1800),  # sem overflow
        (1, 0.0, 24),  # -20%
        (1, 0.75, 33),  # +10%
        (7, 0.0, 1440),  # jitter sobre o teto
    ],
)
def test_atraso_retry(tentativa: int, sorteio: float, segundos: float) -> None:
    assert atraso_retry(tentativa, _CFG, sorteio) == timedelta(seconds=segundos)


# ---- Conferencia (D11) -------------------------------------------------------------------------


def _calculo_que_diverge(
    total: Decimal, pesos: Sequence[int], datas: Sequence[date]
) -> tuple[ParcelaCalculada, ...]:
    ps = calcular_parcelas(total, pesos, datas)
    return (dataclasses.replace(ps[0], valor=ps[0].valor + Decimal("0.01")), *ps[1:])


async def test_conferencia_divergente_vai_para_erro_tecnico_sem_csrf_nem_post() -> None:
    c = Cenario()
    cid, job_id, _ = await _na_fila(c)
    p = c.processador(algoritmo=Algoritmo(ALGORITMO_PARCELAS, _calculo_que_diverge))
    r = await p.processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (E.CONFERENCIA_DIVERGENTE, S.ERRO_TECNICO)
    assert c.gateway.preparos == 0
    assert c.gateway.corpos == []
    assert _envios(c) == []
    assert _job(c, job_id).concluido
    async with c.uow() as uow:
        ultimo = (await uow.eventos.listar(cid))[-1].transicao
    assert ultimo.detalhe == {"parcela": 1, "campo": "Valor", "tentativa": 1}
    assert c.metricas.chamadas == [
        ("conferencia_divergente", {"transicao": "CONFERENCIA_DIVERGENTE", "contract_id": str(cid)})
    ]


async def test_versao_diferente_envia_o_snapshot_sem_conferir() -> None:
    c = Cenario()
    cid, _, snapshot_id = await _na_fila(c)

    def explode(*_: object) -> tuple[ParcelaCalculada, ...]:
        raise AssertionError("nao deveria calcular")

    c.gateway.post = [desfecho(E.SAP_201, status=201, numero="1")]
    r = await c.processador(algoritmo=Algoritmo("maior-resto/2", explode)).processar_proximo()
    assert r is not None
    assert r.para is S.CRIADO
    async with c.uow() as uow:
        eventos = await uow.eventos.listar(cid)
        snapshot = await uow.snapshots.obter(snapshot_id)
    assert eventos[1].transicao.detalhe == {"tentativa": 1, "conferencia": "pulada_versao"}
    assert snapshot is not None
    assert c.gateway.corpos == [corpo_sap(snapshot.contrato)]


async def test_falha_no_preparo_e_nao_classificada_antes_do_envio() -> None:
    c = Cenario()
    cid, job_id, _ = await _na_fila(c)

    def explode(_: object) -> bytes:
        raise ValueError("mapper")

    r = await c.processador(corpo_de=explode).processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (E.FALHA_NAO_CLASSIFICADA_ANTES_ENVIO, S.ERRO_TECNICO)
    assert c.gateway.preparos == 0
    assert _envios(c) == []
    assert _job(c, job_id).concluido
    async with c.uow() as uow:
        ultimo = (await uow.eventos.listar(cid))[-1].transicao
    assert ultimo.detalhe == {"fase": "preparo", "error_class": "ValueError", "tentativa": 1}
    assert [n for n, _ in c.metricas.chamadas] == ["erro_tecnico"]


# ---- Falhas nossas depois do envio (D12) -------------------------------------------------------


async def test_transition_que_recusa_o_201_vira_falha_apos_resposta() -> None:
    c = Cenario()
    cid, job_id, _ = await _na_fila(c)
    c.gateway.post = [desfecho(E.SAP_201, status=201, corpo=b"{}", numero="ABC")]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (E.FALHA_APOS_RESPOSTA, S.INCERTO)
    async with c.uow() as uow:
        ultimo = (await uow.eventos.listar(cid))[-1].transicao
    assert ultimo.detalhe == {
        "fase": "transicao",
        "error_class": "DomainValidationError",
        "tentativa": 1,
    }
    resposta = c.banco.estado.respostas[_envios(c)[0].id]
    assert (resposta.response_status, resposta.response_body) == (201, b"{}")
    assert _job(c, job_id).concluido
    assert [n for n, _ in c.metricas.chamadas] == ["contrato_incerto"]


async def test_gateway_que_levanta_no_post_vai_para_incerto() -> None:
    c = Cenario()
    await _na_fila(c)
    c.gateway.post = [RuntimeError("porta quebrada")]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO, S.INCERTO)
    resposta = c.banco.estado.respostas[_envios(c)[0].id]
    assert (resposta.response_status, resposta.response_body) == (None, None)
    assert (resposta.error_class, resposta.duracao_ms) == ("RuntimeError", 0)


# ---- Estado inesperado e fencing ---------------------------------------------------------------


async def test_contrato_que_saiu_da_fila_descarta_o_job_sem_evento() -> None:
    c = Cenario()
    cid, job_id, _ = await _na_fila(c)
    await c.forcar_status(cid, S.CANCELADO)
    r = await c.processador().processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (None, None)
    assert _job(c, job_id).concluido
    assert await c.eventos(cid) == [E.SUBMETER]
    assert c.gateway.preparos == 0


async def test_worker_que_perdeu_o_lock_nao_grava_o_resultado() -> None:
    c = Cenario()
    cid, job_id, _ = await _na_fila(c)
    c.gateway.post = [desfecho(E.SAP_201, status=201, numero="1")]

    async def recover_rouba_o_lock(ponto: PontoCaos) -> None:
        if ponto is PontoCaos.DEPOIS_DO_POST:
            c.relogio.avancar(LOCK + timedelta(seconds=1))
            assert await c.recuperar() is not None

    r = await c.processador(gancho=recover_rouba_o_lock).processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (None, None)
    assert await c.status(cid) is S.INCERTO
    assert await c.eventos(cid) == [E.SUBMETER, E.WORKER_PEGOU, E.LOCK_EXPIRADO_COM_ENVIO]
    assert c.banco.estado.respostas == {}  # o resultado do worker antigo nao foi gravado
    assert _job(c, job_id).concluido
    assert [n for n, _ in c.metricas.chamadas] == ["contrato_incerto"]


# ---- LIBERAR_REENVIO reusa o snapshot, byte a byte ----------------------------------------------


def _outro_calculo(
    total: Decimal, pesos: Sequence[int], datas: Sequence[date]
) -> tuple[ParcelaCalculada, ...]:
    return tuple(
        dataclasses.replace(p, valor=Decimal("1.00"))
        for p in calcular_parcelas(total, pesos, datas)
    )


async def test_liberar_reenvio_manda_os_mesmos_bytes_mesmo_com_outro_algoritmo() -> None:
    c = Cenario()
    cid, _, snapshot_id = await _na_fila(c)
    c.gateway.post = [desfecho(E.TIMEOUT_APOS_POST)]
    await c.processador().processar_proximo()
    assert await c.status(cid) is S.INCERTO

    # admin (Fase 4): LIBERAR_REENVIO com job novo apontando para o MESMO snapshot
    admin = Ator(ActorKind.ADMIN, "oid-admin")
    t = transition(
        S.INCERTO, E.LIBERAR_REENVIO, ator=admin, justificativa="conferido na VA43: nao existe"
    )
    async with c.uow() as uow:
        atual = await uow.contratos.obter(cid)
        assert atual is not None
        await uow.contratos.atualizar_status(cid, version_esperada=atual.version, para=t.para)
        await uow.eventos.anexar(cid, t, occurred_at=c.relogio.agora())
        await uow.outbox.enfileirar(
            NovoJob(
                id=UUID(int=7),
                contract_id=cid,
                snapshot_id=snapshot_id,
                run_after=c.relogio.agora(),
                correlation_id="corr-2",
            )
        )
        await uow.commit()

    c.gateway.post = [desfecho(E.SAP_201, status=201, numero="1")]
    p = c.processador(algoritmo=Algoritmo("maior-resto/9", _outro_calculo))
    r = await p.processar_proximo()
    assert r is not None
    assert r.para is S.CRIADO
    primeira, segunda = sorted(_envios(c), key=lambda e: e.job_id == UUID(int=7))
    assert segunda.job_id == UUID(int=7)
    assert segunda.snapshot_id == primeira.snapshot_id == snapshot_id
    assert segunda.request_body == primeira.request_body
    assert segunda.request_sha256 == primeira.request_sha256
    assert c.gateway.corpos[0] == c.gateway.corpos[1]


# ---- Alerta (D15) em toda a matriz --------------------------------------------------------------


@pytest.mark.parametrize(("chave", "regra"), list(MATRIZ.items()), ids=lambda x: str(x))
def test_nome_do_alerta_em_toda_a_matriz(
    chave: tuple[ContractStatus, TransitionEvent], regra: Any
) -> None:
    de, evento = chave
    t = dataclasses.replace(
        transition(S.ENVIANDO, E.SAP_4XX_NEGOCIO, ator=W1), de=de, evento=evento, para=regra.para
    )
    if evento is E.CONFERENCIA_DIVERGENTE:
        esperado: str | None = "conferencia_divergente"
    elif regra.para is S.INCERTO:
        esperado = "contrato_incerto"
    elif regra.para is S.ERRO_TECNICO:
        esperado = "erro_tecnico"
    else:
        esperado = None
    assert nome_do_alerta(t) == esperado


async def test_transition_que_recusa_sem_resposta_e_nao_classificada() -> None:
    c = Cenario()
    await _na_fila(c)
    c.gateway.post = [desfecho(E.SAP_201, numero="ABC")]  # sem resposta: so para o teste
    r = await c.processador().processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO, S.INCERTO)


async def test_snapshot_ausente_e_falha_antes_do_envio() -> None:
    c = Cenario()
    cid, job_id, _ = await _na_fila(c)
    job = _job(c, job_id)
    job.novo = dataclasses.replace(job.novo, snapshot_id=UUID(int=1))
    r = await c.processador().processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (E.FALHA_NAO_CLASSIFICADA_ANTES_ENVIO, S.ERRO_TECNICO)
    async with c.uow() as uow:
        ultimo = (await uow.eventos.listar(cid))[-1].transicao
    assert ultimo.detalhe == {"fase": "preparo", "error_class": "SnapshotAusente", "tentativa": 1}
    assert c.gateway.preparos == 0


async def test_lock_perdido_dentro_da_transacao_termina_sem_gravar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.application import fakes

    async def nao_acha(*_: object, **__: object) -> bool:
        return False

    monkeypatch.setattr(fakes._Outbox, "concluir", nao_acha)
    c = Cenario()
    cid, _, _ = await _na_fila(c)
    c.gateway.post = [desfecho(E.SAP_4XX_NEGOCIO, status=400)]
    r = await c.processador().processar_proximo()
    assert r is not None
    assert (r.evento, r.para) == (None, None)  # resultado e fallback nao gravaram
    assert await c.status(cid) is S.ENVIANDO  # o recover decide quando o lock expirar
