"""Partes puras do ``scripts/smoke_dev.py`` (Tarefa 2.12). Nada aqui toca rede nem banco."""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from types import ModuleType

from app.application.snapshot import montar_contrato
from app.settings import WorkerSettings
from tests.conftest import ConfiguraSap


def _script() -> ModuleType:
    caminho = __file__.replace("\\", "/").rsplit("/tests/", 1)[0] + "/scripts/smoke_dev.py"
    spec = importlib.util.spec_from_file_location("smoke_dev", caminho)
    assert spec is not None
    assert spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


smoke = _script()


def test_marca_unica_por_execucao_em_utc() -> None:
    brt = timezone(timedelta(hours=-3))
    assert smoke.marca_da_execucao(datetime(2026, 9, 25, 9, 30, 5, tzinfo=brt)) == (
        "SMOKE-20260925123005"
    )
    assert len(smoke.marca_da_execucao(datetime.now(UTC))) <= 35  # PurchaseOrderByCustomer


def test_entrada_do_payload_de_referencia_com_a_marca() -> None:
    entrada, parcelas = smoke.montar_entrada("SMOKE-1")
    assert entrada["PedidoSysFertil"] == entrada["PurchaseOrderByCustomer"] == "SMOKE-1"
    assert "StatusBlock" not in entrada
    assert "to_FormPag" not in entrada
    assert parcelas.total == Decimal("23299.55")
    assert parcelas.pesos == (1, 1, 1)
    assert parcelas.datas == (date(2026, 9, 4), date(2026, 10, 4), date(2026, 11, 3))
    assert parcelas.form_pag == "K"
    contrato = montar_contrato(entrada, parcelas)  # valida pelo dominio
    assert [p.valor for p in contrato.installments] == [
        Decimal("7766.52"),
        Decimal("7766.52"),
        Decimal("7766.51"),
    ]


def test_confirmacao_exige_a_palavra_exata() -> None:
    assert smoke.confirmar("r", lambda _: " ENVIAR \n") is True
    for resposta in ("enviar", "", "sim", "ENVIAR!"):
        assert smoke.confirmar("r", lambda _, r=resposta: r) is False


async def test_recusa_nao_grava_nem_envia(sap_env: ConfiguraSap) -> None:
    sap_env()  # banco e SAP inalcancaveis: se tentasse usar, o teste falharia
    perguntas: list[str] = []

    def perguntar(texto: str) -> str:
        perguntas.append(texto)
        return "nao"

    assert await smoke.executar(WorkerSettings(), perguntar) == 2
    assert perguntas == ["Digite ENVIAR para criar o contrato no SAP DEV: "]


def test_main_recusa_fora_de_dev(sap_env: ConfiguraSap) -> None:
    sap_env(app_env="qas", creds_via="file")
    assert smoke.main() == 1
