"""Testes de ``app.observability.logging``.

Verifica:
- JSON no stdout com as chaves obrigatorias (``timestamp``, ``level``, ``event``, ``logger``);
- chaves sensiveis (``authorization``, ``password``, ``secret``, ``token``, ``sap_pass``)
  sao substituidas por ``***REDACTED***`` no evento antes da serializacao.
"""

from __future__ import annotations

import json
import logging as std_logging

import pytest
import structlog

from app.observability.logging import configure_logging


@pytest.fixture(autouse=True)
def _reset_logging():
    """Zera config do structlog e handlers stdlib entre testes."""
    structlog.reset_defaults()
    root = std_logging.getLogger()
    prev_handlers = root.handlers[:]
    prev_level = root.level
    yield
    structlog.reset_defaults()
    root.handlers = prev_handlers
    root.setLevel(prev_level)


def _linha_json_do_log(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    saida = capsys.readouterr().out.strip().splitlines()
    assert saida, "esperado ao menos uma linha de log no stdout"
    return json.loads(saida[-1])


def test_log_json_com_chaves_obrigatorias(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO")
    structlog.get_logger("teste.log").info("evento", user_id=1)
    linha = _linha_json_do_log(capsys)
    assert linha["event"] == "evento"
    assert linha["level"] == "info"
    assert linha["logger"] == "teste.log"
    assert "timestamp" in linha
    assert linha["user_id"] == 1


def test_authorization_e_redigido(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO")
    structlog.get_logger("teste.log").info(
        "req",
        authorization="Basic dXNlcjpwYXNz",
        outro_campo="visivel",
    )
    linha = _linha_json_do_log(capsys)
    assert linha["authorization"] == "***REDACTED***"
    assert linha["outro_campo"] == "visivel"


@pytest.mark.parametrize("chave", ["password", "sap_pass", "token", "secret", "Authorization"])
def test_chaves_sensiveis_sao_redigidas(capsys: pytest.CaptureFixture[str], chave: str) -> None:
    configure_logging(level="INFO")
    structlog.get_logger("teste.log").info("evt", **{chave: "valor-secreto-42"})
    linha = _linha_json_do_log(capsys)
    assert linha[chave] == "***REDACTED***"
    assert "valor-secreto-42" not in json.dumps(linha)
