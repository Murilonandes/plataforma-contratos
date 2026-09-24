"""Settings por processo (Fase 2, Tarefa 2.1).

- ``ApiSettings``: so ``APP_ENV``, ``LOG_LEVEL`` e ``DATABASE_URL``. A API nunca
  recebe config nem credencial do SAP.
- ``WorkerSettings``: o mesmo mais a config SAP (guard DEV x PRD, credenciais) e a
  do worker. Guard do D4: ``SAP_LOCK_TIMEOUT_S`` > prazo por tentativa + 60 s, com
  prazo = 4 x (connect + read) (CSRF + POST + refetch do CSRF + POST).
- ``DATABASE_URL`` e credencial: em qas/prd so vem da fonte de arquivo efetiva.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import ApiSettings, WorkerSettings
from tests.conftest import ConfiguraSap

_DB_OK = "postgresql+asyncpg://app:senha-db-9f3@db:5432/contratos"


@pytest.fixture
def api_env(monkeypatch: pytest.MonkeyPatch, isolated_settings_env: Path) -> Path:
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", _DB_OK)
    return isolated_settings_env


def _mensagens(exc: pytest.ExceptionInfo[ValidationError]) -> list[str]:
    return [e["msg"] for e in exc.value.errors()]


# ---- ApiSettings ---------------------------------------------------------------


def test_api_carrega_so_com_app_env_e_banco(api_env: Path) -> None:
    s = ApiSettings()
    assert s.app_env == "dev"
    assert s.database_url.get_secret_value() == _DB_OK
    assert s.log_level == "INFO"


def test_api_nao_tem_nenhum_campo_do_sap(api_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesmo com SAP_* no ambiente, a API nao le nem guarda nada do SAP."""
    monkeypatch.setenv("SAP_USER", "u")
    monkeypatch.setenv("SAP_PASS", "p")
    monkeypatch.setenv("SAP_BASE_URL", "https://s4-dev.acme/x/")
    s = ApiSettings()
    assert set(type(s).model_fields) == {"app_env", "database_url", "log_level"}
    assert not any(k.startswith("sap") for k in s.model_dump())


def test_api_sem_banco_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    with pytest.raises(ValidationError) as exc:
        ApiSettings()
    assert [e["loc"] for e in exc.value.errors()] == [("database_url",)]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://app:x@db/contratos",  # sem o driver async
        "postgres+asyncpg://app:x@db/contratos",
        "sqlite+aiosqlite:///x.db",
        "",
    ],
)
def test_banco_exige_driver_asyncpg(
    api_env: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(ValidationError) as exc:
        ApiSettings()
    assert _mensagens(exc) == ["Value error, DATABASE_URL deve usar o driver postgresql+asyncpg://"]


def test_banco_de_arquivo_com_newline_e_stripado(
    api_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL")
    (api_env / "database_url").write_text(_DB_OK + "\n", encoding="utf-8")
    assert ApiSettings().database_url.get_secret_value() == _DB_OK


@pytest.mark.parametrize("app_env", ["qas", "prd"])
def test_fora_de_dev_banco_so_de_arquivo(
    api_env: Path, monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    with pytest.raises(ValidationError) as exc:
        ApiSettings()
    assert _mensagens(exc) == [
        f"Value error, Em app_env='{app_env}', DATABASE_URL deve vir de arquivo em "
        f"{api_env}, nunca de variavel de ambiente"
    ]
    (api_env / "database_url").write_text(_DB_OK, encoding="utf-8")
    assert ApiSettings().database_url.get_secret_value() == _DB_OK


def test_senha_do_banco_nunca_aparece(api_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = ApiSettings()
    assert "senha-db-9f3" not in repr(s) + str(s) + str(s.model_dump())
    monkeypatch.setenv("APP_ENV", "prd")
    with pytest.raises(ValidationError) as exc:
        ApiSettings()
    assert "senha-db-9f3" not in str(exc.value)


# ---- WorkerSettings --------------------------------------------------------------


def test_worker_defaults_da_fase_2(sap_env: ConfiguraSap) -> None:
    sap_env()
    s = WorkerSettings()
    assert s.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert s.sap_decimal_as_string is True
    assert s.sap_max_tentativas == 5
    assert s.sap_lock_timeout_s == 600.0
    assert s.worker_poll_interval_s == 2.0
    assert s.prazo_tentativa_s == 380.0  # 4 x (5 + 90)


def test_worker_exige_o_banco(sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch) -> None:
    sap_env()
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ValidationError) as exc:
        WorkerSettings()
    assert [e["loc"] for e in exc.value.errors()] == [("database_url",)]


@pytest.mark.parametrize(
    ("connect", "read", "lock", "ok"),
    [
        ("5", "90", "440", False),  # prazo 380 + 60 = 440: precisa ser MAIOR
        ("5", "90", "441", True),
        ("5", "90", "300", False),  # o default antigo da proposta nao passa mais
        ("2", "30", "189", True),  # prazo 128 + 60 = 188
        ("2", "30", "188", False),
    ],
)
def test_guard_do_lock_contra_o_prazo_da_tentativa(
    sap_env: ConfiguraSap,
    monkeypatch: pytest.MonkeyPatch,
    connect: str,
    read: str,
    lock: str,
    ok: bool,
) -> None:
    sap_env()
    monkeypatch.setenv("SAP_TIMEOUT_CONNECT_S", connect)
    monkeypatch.setenv("SAP_TIMEOUT_READ_S", read)
    monkeypatch.setenv("SAP_LOCK_TIMEOUT_S", lock)
    if ok:
        assert WorkerSettings().sap_lock_timeout_s == float(lock)
        return
    prazo = 4 * (float(connect) + float(read))
    with pytest.raises(ValidationError) as exc:
        WorkerSettings()
    assert _mensagens(exc) == [
        f"Value error, SAP_LOCK_TIMEOUT_S ({float(lock):g} s) deve ser maior que o prazo "
        f"por tentativa ({prazo:g} s) + 60 s"
    ]


@pytest.mark.parametrize(
    ("var", "valor"),
    [
        ("SAP_MAX_TENTATIVAS", "0"),
        ("SAP_MAX_TENTATIVAS", "-1"),
        ("WORKER_POLL_INTERVAL_S", "0"),
        ("SAP_TIMEOUT_CONNECT_S", "0"),
        ("SAP_TIMEOUT_READ_S", "-5"),
        ("SAP_LOCK_TIMEOUT_S", "0"),
    ],
)
def test_numeros_do_worker_precisam_ser_positivos(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch, var: str, valor: str
) -> None:
    sap_env()
    monkeypatch.setenv(var, valor)
    with pytest.raises(ValidationError) as exc:
        WorkerSettings()
    assert (var.lower(),) in [e["loc"] for e in exc.value.errors()]


@pytest.mark.parametrize(("valor", "esperado"), [("false", False), ("0", False), ("true", True)])
def test_flag_decimal_como_string(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch, valor: str, esperado: bool
) -> None:
    sap_env()
    monkeypatch.setenv("SAP_DECIMAL_AS_STRING", valor)
    assert WorkerSettings().sap_decimal_as_string is esperado


def test_worker_em_prd_exige_banco_de_arquivo_alem_do_sap(sap_env: ConfiguraSap) -> None:
    sap_env(app_env="prd", base_url="https://s4-prd.acme/x/", creds_via="file")
    assert WorkerSettings().app_env == "prd"


def test_worker_em_prd_com_banco_de_env_falha(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch, isolated_settings_env: Path
) -> None:
    sap_env(app_env="prd", base_url="https://s4-prd.acme/x/", creds_via="file")
    (isolated_settings_env / "database_url").unlink()
    monkeypatch.setenv("DATABASE_URL", _DB_OK)
    with pytest.raises(ValidationError) as exc:
        WorkerSettings()
    assert _mensagens(exc) == [
        f"Value error, Em app_env='prd', DATABASE_URL deve vir de arquivo em "
        f"{isolated_settings_env}, nunca de variavel de ambiente"
    ]
