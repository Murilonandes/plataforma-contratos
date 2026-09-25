"""Adapter ``MetricasEmLog`` (D15): JSON, nivel ERROR, campo ``alert``."""

from __future__ import annotations

import json

import pytest

from app.application.ports import Metrics
from app.observability.logging import configure_logging
from app.observability.metricas import ALERTAS, MetricasEmLog


def test_cumpre_a_porta() -> None:
    porta: Metrics = MetricasEmLog()
    assert isinstance(porta, MetricasEmLog)


def test_alertas_conhecidos() -> None:
    assert (
        frozenset(
            {"conferencia_divergente", "contrato_incerto", "erro_tecnico", "sap_heartbeat_atrasado"}
        )
        == ALERTAS
    )


@pytest.mark.parametrize("alerta", sorted(ALERTAS))
def test_linha_json_error_com_alert_e_labels(
    alerta: str, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    MetricasEmLog().incrementar(alerta, transicao="TIMEOUT_APOS_POST", contract_id="c-1")
    (linha,) = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert {k: linha[k] for k in ("event", "level", "alert", "transicao", "contract_id")} == {
        "event": "alerta",
        "level": "error",
        "alert": alerta,
        "transicao": "TIMEOUT_APOS_POST",
        "contract_id": "c-1",
    }
    assert set(linha) == {
        "event",
        "level",
        "logger",
        "timestamp",
        "alert",
        "transicao",
        "contract_id",
    }


def test_alerta_desconhecido_e_bug() -> None:
    with pytest.raises(ValueError) as exc:  # noqa: PT011 — mensagem conferida abaixo
        MetricasEmLog().incrementar("qualquer")
    assert str(exc.value) == "alerta desconhecido: qualquer"
