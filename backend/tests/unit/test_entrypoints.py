"""Entrypoints fazem o bootstrap do logging antes de qualquer Settings.

Garante o contrato "log 100% JSON" mesmo para o que acontece durante a
instanciacao do Settings/app (ex.: warnings de bibliotecas, erro fail-closed).
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import pytest

from app.entrypoints import api, worker
from app.settings import Settings
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


def test_worker_loga_em_json_e_sai_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 0
    assert _linhas_json(capsys)[-1]["event"] == "worker stub — Fase 2"


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
        sap_pass=senha,
    )

    def uvicorn_run_falso(*_args: object, **_kwargs: object) -> None:
        Settings()  # o que a factory faz no uvicorn real

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
    assert "SAP_USER deve vir de arquivo" in json.dumps(falha["erros"])
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
