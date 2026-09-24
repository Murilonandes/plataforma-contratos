"""Tipos de valor das portas (``application/ports.py``): invariantes.

As portas carregam as garantias aprovadas da Fase 2 no proprio tipo:
- ``EnvioRegistrado``: bytes exatos do POST + sha256 que bate (D7);
- ``JobPego``: ``lock_token`` por pega (fencing, D4);
- ``DesfechoCsrf``/``DesfechoPost``: o gateway so pode devolver eventos possiveis
  naquela fase (nada "antes do envio" depois do marcador, e vice-versa);
- instantes sempre com fuso (D5: o relogio vem do ``Clock``).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, tzinfo
from uuid import UUID, uuid4

import pytest

from app.application.ports import (
    EVENTOS_CSRF,
    EVENTOS_POST,
    DesfechoCsrf,
    DesfechoPost,
    EnvioRegistrado,
    JobPego,
    NovoJob,
    RespostaSap,
)
from app.domain.enums import TransitionEvent

E = TransitionEvent
AGORA = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
INGENUO = datetime(2026, 9, 25, 12, 0)  # noqa: DTZ001 - de proposito, sem fuso


def _envio(**extra: object) -> EnvioRegistrado:
    corpo = b'{"SalesContractType":"ZCON"}'
    base: dict[str, object] = {
        "id": uuid4(),
        "job_id": uuid4(),
        "contract_id": uuid4(),
        "snapshot_id": uuid4(),
        "tentativa": 1,
        "request_sent_at": AGORA,
        "request_body": corpo,
        "request_sha256": hashlib.sha256(corpo).hexdigest(),
    }
    return EnvioRegistrado(**{**base, **extra})  # type: ignore[arg-type]


# ---- EnvioRegistrado (D7) ----------------------------------------------------------


def test_envio_criar_calcula_o_hash_dos_bytes_exatos() -> None:
    corpo = b'{"Valor":7766.50}'
    e = EnvioRegistrado.criar(
        job_id=uuid4(),
        contract_id=uuid4(),
        snapshot_id=uuid4(),
        tentativa=2,
        request_sent_at=AGORA,
        request_body=corpo,
    )
    assert e.request_body == corpo
    assert e.request_sha256 == hashlib.sha256(corpo).hexdigest()
    assert e.tentativa == 2


def test_envio_criar_preserva_as_referencias_e_gera_id_novo() -> None:
    job, contrato, snapshot = uuid4(), uuid4(), uuid4()
    kwargs = {
        "job_id": job,
        "contract_id": contrato,
        "snapshot_id": snapshot,
        "tentativa": 1,
        "request_sent_at": AGORA,
        "request_body": b"{}",
    }
    a = EnvioRegistrado.criar(**kwargs)  # type: ignore[arg-type]
    b = EnvioRegistrado.criar(**kwargs)  # type: ignore[arg-type]
    assert (a.job_id, a.contract_id, a.snapshot_id, a.request_sent_at) == (
        job,
        contrato,
        snapshot,
        AGORA,
    )
    assert isinstance(a.id, UUID)
    assert a.id != b.id


def test_envio_recusa_hash_que_nao_bate() -> None:
    with pytest.raises(ValueError, match=r"^request_sha256 nao corresponde a request_body$"):
        _envio(request_sha256="0" * 64)


def test_envio_recusa_corpo_que_nao_e_bytes() -> None:
    with pytest.raises(TypeError, match=r"^request_body precisa ser bytes$"):
        _envio(request_body='{"a":1}')


@pytest.mark.parametrize("tentativa", [0, -1])
def test_envio_recusa_tentativa_menor_que_1(tentativa: int) -> None:
    with pytest.raises(ValueError, match=r"^tentativa precisa ser >= 1$"):
        _envio(tentativa=tentativa)


# ---- Instantes com fuso (D5) -------------------------------------------------------


def test_envio_recusa_instante_sem_fuso() -> None:
    with pytest.raises(ValueError, match=r"^request_sent_at precisa ter fuso horario$"):
        _envio(request_sent_at=INGENUO)


class _FusoSemOffset(tzinfo):
    """tzinfo presente, mas utcoffset() None: tambem nao e instante com fuso."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


def test_instante_com_tzinfo_sem_offset_e_recusado() -> None:
    with pytest.raises(ValueError, match=r"^request_sent_at precisa ter fuso horario$"):
        _envio(request_sent_at=datetime(2026, 9, 25, 12, 0, tzinfo=_FusoSemOffset()))


def test_job_pego_e_novo_job_recusam_instante_sem_fuso() -> None:
    with pytest.raises(ValueError, match=r"^locked_until precisa ter fuso horario$"):
        JobPego(
            id=uuid4(),
            contract_id=uuid4(),
            snapshot_id=uuid4(),
            tentativa=1,
            lock_token=uuid4(),
            locked_until=INGENUO,
            correlation_id="c",
        )
    with pytest.raises(ValueError, match=r"^run_after precisa ter fuso horario$"):
        NovoJob(
            id=uuid4(),
            contract_id=uuid4(),
            snapshot_id=uuid4(),
            run_after=INGENUO,
            correlation_id="c",
        )


# ---- Desfechos do gateway: so eventos possiveis na fase ------------------------------


def test_eventos_do_csrf_sao_todos_antes_do_envio() -> None:
    assert (
        frozenset({E.FALHA_ANTES_POST, E.SAP_4XX_TECNICO, E.FALHA_NAO_CLASSIFICADA_ANTES_ENVIO})
        == EVENTOS_CSRF
    )


def test_eventos_do_post() -> None:
    """ConnectError no POST ainda e FALHA_ANTES_POST (§4): o body nao saiu."""
    assert (
        frozenset(
            {
                E.SAP_201,
                E.SAP_4XX_NEGOCIO,
                E.SAP_4XX_TECNICO,
                E.FALHA_ANTES_POST,
                E.TIMEOUT_APOS_POST,
                E.CONEXAO_CAIDA_APOS_POST,
                E.SAP_5XX_APOS_POST,
                E.FALHA_APOS_RESPOSTA,
                E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO,
            }
        )
        == EVENTOS_POST
    )


def test_desfecho_csrf_ok_e_sem_evento() -> None:
    assert DesfechoCsrf.ok().evento is None
    assert DesfechoCsrf.ok().detalhe == {}


@pytest.mark.parametrize("evento", sorted(set(TransitionEvent) - EVENTOS_CSRF))
def test_desfecho_csrf_recusa_evento_fora_da_fase(evento: TransitionEvent) -> None:
    with pytest.raises(ValueError, match=rf"^evento {evento.value} nao e possivel no CSRF$"):
        DesfechoCsrf(evento=evento, detalhe={})


@pytest.mark.parametrize("evento", sorted(set(TransitionEvent) - EVENTOS_POST))
def test_desfecho_post_recusa_evento_fora_da_fase(evento: TransitionEvent) -> None:
    with pytest.raises(
        ValueError, match=rf"^evento {evento.value} nao e possivel depois do marcador$"
    ):
        DesfechoPost(evento=evento, sap_contract_number=None, resposta=None, duracao_ms=0)


def test_desfecho_post_201_exige_numero_e_os_demais_nao_tem() -> None:
    resposta = RespostaSap(status=201, headers={}, corpo=b"{}")
    ok = DesfechoPost(
        evento=E.SAP_201, sap_contract_number="0040001234", resposta=resposta, duracao_ms=10
    )
    assert ok.sap_contract_number == "0040001234"
    with pytest.raises(ValueError, match=r"^SAP_201 exige sap_contract_number$"):
        DesfechoPost(evento=E.SAP_201, sap_contract_number=None, resposta=resposta, duracao_ms=10)
    with pytest.raises(ValueError, match=r"^so SAP_201 tem sap_contract_number$"):
        DesfechoPost(
            evento=E.FALHA_APOS_RESPOSTA,
            sap_contract_number="0040001234",
            resposta=resposta,
            duracao_ms=10,
        )


def test_desfecho_post_aceita_duracao_zero() -> None:
    d = DesfechoPost(
        evento=E.TIMEOUT_APOS_POST, sap_contract_number=None, resposta=None, duracao_ms=0
    )
    assert d.duracao_ms == 0


def test_desfecho_post_guarda_detalhe_imutavel() -> None:
    original: dict[str, str | int] = {"error_class": "ReadTimeout", "fase": "post"}
    d = DesfechoPost(
        evento=E.TIMEOUT_APOS_POST,
        sap_contract_number=None,
        resposta=None,
        duracao_ms=5,
        detalhe=original,
    )
    original["fase"] = "mudou"
    assert dict(d.detalhe) == {"error_class": "ReadTimeout", "fase": "post"}
    with pytest.raises(TypeError):
        d.detalhe["x"] = 1  # type: ignore[index]


def test_desfecho_csrf_guarda_detalhe_imutavel() -> None:
    original: dict[str, str | int] = {"status": 401}
    d = DesfechoCsrf(evento=E.SAP_4XX_TECNICO, detalhe=original)
    original["status"] = 500
    assert dict(d.detalhe) == {"status": 401}


def test_desfecho_post_recusa_duracao_negativa() -> None:
    with pytest.raises(ValueError, match=r"^duracao_ms precisa ser >= 0$"):
        DesfechoPost(
            evento=E.TIMEOUT_APOS_POST, sap_contract_number=None, resposta=None, duracao_ms=-1
        )


def test_resposta_sap_guarda_o_corpo_cru_e_headers_imutaveis() -> None:
    original = {"sap-messages": "[]"}
    r = RespostaSap(status=201, headers=original, corpo=b"nao-json")
    original["sap-messages"] = "mudou"
    assert r.corpo == b"nao-json"
    assert dict(r.headers) == {"sap-messages": "[]"}
    with pytest.raises(TypeError):
        r.headers["x"] = "y"  # type: ignore[index]
