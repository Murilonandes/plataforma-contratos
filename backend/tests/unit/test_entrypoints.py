"""Entrypoints fazem o bootstrap do logging antes de qualquer Settings.

Garante o contrato "log 100% JSON" mesmo para o que acontece durante a
instanciacao do Settings/app (ex.: warnings de bibliotecas, erro fail-closed).
"""

from __future__ import annotations

import asyncio
import json
import signal
import warnings
from typing import Any

import pytest
import structlog

from app.entrypoints import api, worker
from app.settings import ApiSettings, WorkerSettings
from tests.conftest import ConfiguraSap


def _linhas_json(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    saida = [linha for linha in capsys.readouterr().out.splitlines() if linha.strip()]
    assert saida, "esperado ao menos uma linha no stdout"
    return [json.loads(linha) for linha in saida]


def test_api_configura_logging_antes_de_subir_a_app(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def uvicorn_run_falso(*_args: object, **_kwargs: object) -> None:
        # No uvicorn real, e aqui que a factory roda e o Settings e instanciado.
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("warning durante o load do Settings", UserWarning, stacklevel=1)

    monkeypatch.setattr(api.uvicorn, "run", uvicorn_run_falso)
    api.main()
    linhas = _linhas_json(capsys)
    assert any("warning durante o load do Settings" in str(x["event"]) for x in linhas)


def test_worker_carrega_settings_roda_o_laco_e_sai_zero(
    capsys: pytest.CaptureFixture[str], sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch
) -> None:
    sap_env()
    recebidos: list[WorkerSettings] = []

    async def rodar_falso(settings: WorkerSettings) -> None:
        recebidos.append(settings)
        structlog.get_logger("teste").info("laco rodou")

    monkeypatch.setattr(worker, "rodar", rodar_falso)
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 0
    assert len(recebidos) == 1
    assert _linhas_json(capsys)[-1]["event"] == "laco rodou"


def test_worker_que_falha_no_laco_sai_1_em_json(
    capsys: pytest.CaptureFixture[str], sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch
) -> None:
    sap_env()

    async def rodar_falso(_: WorkerSettings) -> None:
        raise RuntimeError("quebrou")

    monkeypatch.setattr(worker, "rodar", rodar_falso)
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 1
    assert _linhas_json(capsys)[-1]["event"] == "worker_falhou"


async def test_rodar_monta_tudo_e_para_limpo_sem_rede(sap_env: ConfiguraSap) -> None:
    """Com o ``parar`` ja ligado, monta engine, client e gateway e sai sem tocar no banco."""
    sap_env()
    settings = WorkerSettings()
    antes = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    parar = asyncio.Event()
    parar.set()
    await asyncio.wait_for(worker.rodar(settings, parar), 5)
    assert {s: signal.getsignal(s) for s in antes} == antes  # handlers restaurados


def test_config_sap_vem_do_settings(sap_env: ConfiguraSap) -> None:
    sap_env(sap_user="u-tec", sap_pass="p-tec")
    c = worker.config_sap(WorkerSettings())
    assert (c.base_url, c.sap_client, c.usuario, c.senha) == (
        "https://s4-dev.acme/path/",
        "300",
        "u-tec",
        "p-tec",
    )
    assert (c.timeout_connect_s, c.timeout_read_s, c.decimal_as_string) == (5.0, 90.0, True)
    assert "p-tec" not in repr(c)


def test_worker_sem_config_sai_1_em_json_sem_senha(
    capsys: pytest.CaptureFixture[str], sap_env: ConfiguraSap
) -> None:
    """Fail-closed: o worker carrega WorkerSettings antes de qualquer coisa."""
    senha = "senha-worker-nao-vaza-7c1d"
    sap_env(
        app_env="prd",
        base_url="https://s4-prd.acme/x/",
        creds_via="env",
        sap_pass=senha,
    )
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 1
    saida = capsys.readouterr()
    assert senha not in saida.out + saida.err
    falha = [json.loads(x) for x in saida.out.splitlines() if x.strip()][-1]
    assert falha["level"] == "critical"
    assert falha["event"] == "startup_falhou_config_invalida"
    assert "SAP_USER deve vir de arquivo" in json.dumps(falha["erros"])
    assert saida.err == ""


def test_api_falha_de_config_no_startup_sai_1_em_json_sem_senha(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    sap_env: ConfiguraSap,
) -> None:
    senha = "senha-startup-nao-vaza-91ab"
    sap_env(
        app_env="prd",
        base_url="https://s4-prd.acme/x/",
        prd_hosts="s4-prd.acme",
        creds_via="env",
        database_url=f"postgresql+asyncpg://app:{senha}@db:5432/contratos",
    )

    def uvicorn_run_falso(*_args: object, **_kwargs: object) -> None:
        ApiSettings()  # o que a factory faz no uvicorn real

    monkeypatch.setattr(api.uvicorn, "run", uvicorn_run_falso)
    with pytest.raises(SystemExit) as exc:
        api.main()
    assert exc.value.code == 1
    saida = capsys.readouterr()
    assert senha not in saida.out + saida.err
    linhas = [json.loads(x) for x in saida.out.splitlines() if x.strip()]
    falha = linhas[-1]
    assert falha["level"] == "critical"
    assert falha["event"] == "startup_falhou_config_invalida"
    assert "DATABASE_URL deve vir de arquivo" in json.dumps(falha["erros"])
    assert saida.err == ""


def test_api_erro_inesperado_no_startup_sai_1_em_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def uvicorn_run_falso(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("porta ocupada")

    monkeypatch.setattr(api.uvicorn, "run", uvicorn_run_falso)
    with pytest.raises(SystemExit) as exc:
        api.main()
    assert exc.value.code == 1
    falha = _linhas_json(capsys)[-1]
    assert falha["event"] == "startup_falhou"
    assert "porta ocupada" in falha["exception"]
