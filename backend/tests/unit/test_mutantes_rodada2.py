"""Testes que matam os mutantes que sobreviveram na segunda revisao.

Cada teste falha se a linha de protecao correspondente for removida (verificado
por mutacao). Varios deles isolam uma protecao que, na suite anterior, so
estava coberta "por tabela" por outra camada de defesa.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI
from pydantic import ValidationError
from pydantic_settings.sources.providers.secrets import SecretsSettingsSource

from app.entrypoints import api
from app.observability.logging import REDACTED, configure_logging
from app.observability.middleware import CorrelationIdMiddleware
from app.settings import Settings
from tests.conftest import ConfiguraSap

_PRD = {"app_env": "prd", "base_url": "https://s4-prd.acme/x/", "prd_hosts": "s4-prd.acme"}


def _linhas(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]


# ---- api.py: errors(include_input=False) no log de startup -------------------


def test_log_de_startup_nao_carrega_input_dos_erros(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, sap_env: ConfiguraSap
) -> None:
    sap_env(sap_user="USR-NAO-PODE-ECOAR", sap_pass="PASS-NAO-PODE-ECOAR")
    monkeypatch.delenv("SAP_CLIENT")  # erro "missing": o input e o dict inteiro do env

    def uvicorn_run_falso(*_a: object, **_k: object) -> None:
        Settings()

    monkeypatch.setattr(api.uvicorn, "run", uvicorn_run_falso)
    with pytest.raises(SystemExit):
        api.main()
    falha = _linhas(capsys)[-1]
    assert falha["event"] == "startup_falhou_config_invalida"
    assert falha["erros"], "esperado ao menos um erro"
    assert all("input" not in erro for erro in falha["erros"])
    assert "USR-NAO-PODE-ECOAR" not in json.dumps(falha)


# ---- settings.py: valor carregado == conteudo do arquivo ---------------------


def test_prd_rejeita_valor_lido_de_arquivo_diferente_do_validado(
    sap_env: ConfiguraSap, isolated_settings_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simula a fonte case-insensitive lendo outro arquivo (SAP_PASS vs sap_pass
    num filesystem case-sensitive), que nao da para criar no Windows."""
    sap_env(**_PRD, creds_via="file", sap_pass="p-validado")
    (isolated_settings_env / "SAP_PASS.variante").write_text("p-outro", encoding="utf-8")
    original = SecretsSettingsSource.find_case_path.__func__  # type: ignore[attr-defined]

    def find_case_path_falso(
        cls: type[SecretsSettingsSource], dir_path: Path, file_name: str, case_sensitive: bool
    ) -> Path | None:
        if file_name.lower() == "sap_pass":
            return dir_path / "SAP_PASS.variante"
        return original(cls, dir_path, file_name, case_sensitive)  # type: ignore[no-any-return]

    monkeypatch.setattr(SecretsSettingsSource, "find_case_path", classmethod(find_case_path_falso))
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_PASS carregado nao veio do arquivo" in str(exc.value)


# ---- settings.py: multiplos secrets dirs --------------------------------------


def test_prd_rejeita_lista_de_secrets_dirs_mesmo_incluindo_o_configurado(
    sap_env: ConfiguraSap, isolated_settings_env: Path, tmp_path: Path
) -> None:
    sap_env(**_PRD, creds_via="file", sap_user="u", sap_pass="p")
    outro = tmp_path / "outro"
    outro.mkdir()
    (outro / "sap_user").write_text("u", encoding="utf-8")
    (outro / "sap_pass").write_text("p", encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        Settings(_secrets_dir=[str(isolated_settings_env), str(outro)])  # type: ignore[call-arg]
    assert "secrets_dir efetivo difere do configurado" in str(exc.value)


# ---- middleware.py: clear_contextvars no inicio do dispatch -------------------


@pytest.fixture
async def cliente_ctx() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/ctx")
    async def ctx() -> dict[str, object]:
        return {"chaves": sorted(structlog.contextvars.get_contextvars())}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://teste") as c:
        yield c


async def test_request_nao_herda_contexto_de_log_anterior(
    cliente_ctx: httpx.AsyncClient,
) -> None:
    structlog.contextvars.bind_contextvars(contract_id="de-outro-request")
    try:
        resp = await cliente_ctx.get("/ctx")
    finally:
        structlog.contextvars.clear_contextvars()
    assert resp.json()["chaves"] == ["correlation_id"]


# ---- logging.py: limite de profundidade ---------------------------------------


def test_aninhamento_alem_do_limite_vira_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    profundo: dict[str, Any] = {"folha": "valor-no-fundo"}
    for _ in range(30):
        profundo = {"n": profundo}
    structlog.get_logger("t").info("evt", dados=profundo)
    bruto = json.dumps(_linhas(capsys)[-1])
    assert "valor-no-fundo" not in bruto
    assert REDACTED in bruto


# ---- logging.py: KeyboardInterrupt no excepthook -----------------------------


def test_keyboard_interrupt_vai_para_o_hook_padrao_e_nao_vira_critical(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_logging("INFO")
    chamados: list[type[BaseException]] = []
    monkeypatch.setattr(sys, "__excepthook__", lambda tipo, *_: chamados.append(tipo))
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert chamados == [KeyboardInterrupt]
    assert not [x for x in _linhas(capsys) if x.get("event") == "excecao_nao_tratada"]


# ---- logging.py: bytes sao DECODIFICADOS (nao so repr mascarado) -------------


def test_bytes_sao_decodificados_e_nao_viram_repr(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info(
        "evt", corpo=bytearray(b"Authorization: Bearer abc"), outro=b"\xff ok"
    )
    linha = _linhas(capsys)[-1]
    assert linha["corpo"] == f"Authorization: {REDACTED}"
    assert linha["outro"] == "\ufffd ok"


# ---- settings.py: motivo especifico de rejeicao do hostname ------------------


@pytest.mark.parametrize(
    ("entrada", "motivo"),
    [
        ("s4-prd-acm\u00e9.com", "so ASCII"),
        ("stra\u00dfe.de", "so ASCII"),
        ("10.0.0.5", "IP literal"),
        ("fd00::5", "IP literal"),
        ("::ffff:10.0.0.5", "IP literal"),
        ("fe80::1%eth0", "IP literal"),
        ("xn--s4-prd-acm-k7a.com", "xn--"),
        ("10.0.5", "ultimo rotulo numerico"),
    ],
)
def test_prd_hosts_rejeitado_com_motivo_especifico(
    sap_env: ConfiguraSap, entrada: str, motivo: str
) -> None:
    sap_env(prd_hosts=entrada)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert motivo in str(exc.value)
