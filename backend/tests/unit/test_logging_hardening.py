"""Hardening do logging apos a revisao de seguranca da Fase 0.

- Achado 2: redacao recursiva, case-insensitive, por padrao de chave + regex
  de "Basic ..."/"Bearer ..." em qualquer string, rodando DEPOIS do
  format_exc_info (pega traceback).
- Achado 3: LOG_LEVEL invalido nao derruba o bootstrap com texto cru; o
  Settings valida (strip + upper, Literal).
- Achado 8: sys.excepthook / threading.excepthook emitem JSON; o worker
  respeita LOG_LEVEL e loga falha em JSON.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from typing import Any

import pytest
import structlog
from pydantic import ValidationError

from app.entrypoints import api, worker
from app.observability.logging import REDACTED, configure_logging
from app.settings import WorkerSettings
from tests.conftest import ConfiguraSap

_BASIC = "Basic dXNlcjpwYXNz"
_SEGREDO = "dXNlcjpwYXNz"


def _linhas(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    saida = capsys.readouterr()
    assert saida.err == "", f"stderr deveria estar vazio: {saida.err!r}"
    return [json.loads(x) for x in saida.out.splitlines() if x.strip()]


def _ultima(capsys: pytest.CaptureFixture[str]) -> tuple[dict[str, Any], str]:
    linhas = _linhas(capsys)
    assert linhas, "esperado ao menos uma linha de log"
    return linhas[-1], json.dumps(linhas[-1])


# ---- Achado 2: denylist ------------------------------------------------------


def test_header_authorization_aninhado_e_redigido(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info(
        "req", headers={"Authorization": _BASIC, "Cookie": "SAP_SESSIONID=abc", "Accept": "json"}
    )
    linha, bruto = _ultima(capsys)
    assert linha["headers"] == {"Authorization": REDACTED, "Cookie": REDACTED, "Accept": "json"}
    assert _SEGREDO not in bruto
    assert "SAP_SESSIONID" not in bruto


@pytest.mark.parametrize(
    "chave",
    [
        "x-api-key",
        "Proxy-Authorization",
        "api_key",
        "access_token",
        "x-csrf-token",
        "set-cookie",
        "client_secret",
        "Credential",
        "SAP_PASS",
        "password",
    ],
)
def test_variantes_de_chave_sao_redigidas(capsys: pytest.CaptureFixture[str], chave: str) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info("evt", **{chave: "valor-secreto-42"})
    linha, bruto = _ultima(capsys)
    assert linha[chave] == REDACTED
    assert "valor-secreto-42" not in bruto


def test_lista_de_tuplas_de_headers_e_redigida(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info(
        "req", headers=[("authorization", "qualquer-coisa"), ("accept", "json")]
    )
    linha, bruto = _ultima(capsys)
    assert "qualquer-coisa" not in bruto
    assert ["accept", "json"] in linha["headers"]


def test_basic_e_bearer_em_string_livre_sao_mascarados(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info(
        f"chamando SAP com Authorization: {_BASIC}", detalhe="token bearer abc.def-ghi"
    )
    linha, bruto = _ultima(capsys)
    assert _SEGREDO not in bruto
    assert "abc.def-ghi" not in bruto
    # a regra de "authorization" mascara o valor inteiro (mais forte que so o Basic)
    assert linha["event"] == f"chamando SAP com Authorization: {REDACTED}"


def test_traceback_de_log_exception_e_mascarado(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    try:
        raise RuntimeError(f"401 for Authorization: {_BASIC}")
    except RuntimeError:
        structlog.get_logger("t").exception("falhou")
    linha, bruto = _ultima(capsys)
    assert "RuntimeError" in linha["exception"]
    assert _SEGREDO not in bruto


def test_traceback_de_logger_stdlib_tambem_e_mascarado(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    try:
        raise RuntimeError(f"Bearer {_SEGREDO}")
    except RuntimeError:
        logging.getLogger("lib.qualquer").exception("falha na lib")
    _, bruto = _ultima(capsys)
    assert _SEGREDO not in bruto


def test_contextvar_aninhado_e_redigido(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.contextvars.bind_contextvars(auth={"token": "tk-123"})
    try:
        structlog.get_logger("t").info("evt")
    finally:
        structlog.contextvars.clear_contextvars()
    _, bruto = _ultima(capsys)
    assert "tk-123" not in bruto


def test_campos_comuns_nao_sao_redigidos(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info("evt", contract_id="c-1", status=201, duration_ms=12)
    linha, _ = _ultima(capsys)
    assert (linha["contract_id"], linha["status"], linha["duration_ms"]) == ("c-1", 201, 12)


# ---- Achado 3: LOG_LEVEL -----------------------------------------------------


@pytest.mark.parametrize(("valor", "esperado"), [(" info", "INFO"), ("debug\n", "DEBUG")])
def test_settings_normaliza_log_level(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch, valor: str, esperado: str
) -> None:
    sap_env()
    monkeypatch.setenv("LOG_LEVEL", valor)
    assert WorkerSettings().log_level == esperado


def test_settings_rejeita_log_level_invalido(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch
) -> None:
    sap_env()
    monkeypatch.setenv("LOG_LEVEL", "verbose")
    with pytest.raises(ValidationError):
        WorkerSettings()


def test_configure_logging_com_nivel_invalido_nao_quebra_e_avisa_em_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("verbose")
    linha, _ = _ultima(capsys)
    assert linha["event"] == "log_level_invalido"
    assert logging.getLogger().level == logging.INFO


def test_api_com_log_level_invalido_sai_1_em_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, sap_env: ConfiguraSap
) -> None:
    sap_env()
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    def uvicorn_run_falso(*_a: object, **_k: object) -> None:
        WorkerSettings()

    monkeypatch.setattr(api.uvicorn, "run", uvicorn_run_falso)
    with pytest.raises(SystemExit) as exc:
        api.main()
    assert exc.value.code == 1
    assert _linhas(capsys)[-1]["event"] == "startup_falhou_config_invalida"


# ---- Achado 8: excepthooks e worker -----------------------------------------


def test_excepthook_emite_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    try:
        raise ValueError(f"boom {_BASIC}")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    linha, bruto = _ultima(capsys)
    assert linha["event"] == "excecao_nao_tratada"
    assert linha["level"] == "critical"
    assert "ValueError" in linha["exception"]
    assert _SEGREDO not in bruto


def test_threading_excepthook_emite_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")

    def alvo() -> None:
        raise RuntimeError("falha na thread")

    t = threading.Thread(target=alvo, name="thread-teste")
    t.start()
    t.join()
    linha, _ = _ultima(capsys)
    assert linha["event"] == "excecao_nao_tratada_em_thread"
    assert linha["thread"] == "thread-teste"
    assert "falha na thread" in linha["exception"]


def test_worker_respeita_log_level(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, sap_env: ConfiguraSap
) -> None:
    sap_env()
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 0
    assert _linhas(capsys) == []  # o info do stub fica abaixo do nivel


def test_worker_falha_sai_1_em_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, sap_env: ConfiguraSap
) -> None:
    sap_env()

    def explode(_settings: object) -> None:
        raise RuntimeError("loop do outbox caiu")

    monkeypatch.setattr(worker, "_executar", explode)
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 1
    linha, _ = _ultima(capsys)
    assert linha["event"] == "worker_falhou"
    assert "loop do outbox caiu" in linha["exception"]


def test_redacao_em_varios_niveis_de_aninhamento(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info(
        "req",
        tentativas=[{"request": {"headers": {"Authorization": "segredo-profundo"}}}],
    )
    _, bruto = _ultima(capsys)
    assert "segredo-profundo" not in bruto
    assert REDACTED in bruto
