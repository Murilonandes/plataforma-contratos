"""Fixtures compartilhadas de teste.

Isola o ambiente de cada teste: aponta o ``secrets_dir`` do ``Settings`` para
um diretorio temporario (evita depender de ``/run/secrets`` na maquina do dev)
e limpa qualquer ``SAP_*`` / ``DATABASE_URL`` / ``APP_ENV`` / ``LOG_LEVEL`` /
``WORKER_*`` herdado do shell.

Perfis do Hypothesis: ``dev`` (default, rapido) e ``ci`` (mais exemplos e
``derandomize=True``: o mesmo commit gera os mesmos exemplos, sem flaky).
Selecionado por ``HYPOTHESIS_PROFILE`` (o CI usa ``ci``).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import structlog
from hypothesis import HealthCheck, settings

settings.register_profile("dev", max_examples=100)
settings.register_profile(
    "ci",
    max_examples=1000,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

_ENV_VARS_ISOLADAS = (
    "APP_ENV",
    "DATABASE_URL",
    "SAP_BASE_URL",
    "SAP_CLIENT",
    "SAP_PRD_HOSTS",
    "SAP_USER",
    "SAP_PASS",
    "SAP_TIMEOUT_CONNECT_S",
    "SAP_TIMEOUT_READ_S",
    "SAP_DECIMAL_AS_STRING",
    "SAP_MAX_TENTATIVAS",
    "SAP_LOCK_TIMEOUT_S",
    "WORKER_POLL_INTERVAL_S",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def isolated_settings_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Sanitiza env e redireciona ``secrets_dir`` dos settings pra ``tmp_path``."""
    from app.settings import ApiSettings, WorkerSettings

    secrets_dir = tmp_path / "run_secrets"
    secrets_dir.mkdir()
    for cls in (ApiSettings, WorkerSettings):  # model_config e copiado por classe
        monkeypatch.setitem(cls.model_config, "secrets_dir", str(secrets_dir))

    for var in _ENV_VARS_ISOLADAS:
        monkeypatch.delenv(var, raising=False)

    return secrets_dir


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    """Restaura structlog, TODOS os loggers stdlib, warnings e excepthooks.

    Necessario porque ha codigo que mexe no logging global durante os testes
    (ex.: ``importlinter.cli`` roda ``dictConfig`` com
    ``disable_existing_loggers``, o que desligava o ``py.warnings`` para os
    testes seguintes).
    """
    root = logging.getLogger()
    loggers = [
        root,
        *(
            lg
            for lg in logging.Logger.manager.loggerDict.values()
            if isinstance(lg, logging.Logger)
        ),
    ]
    snapshot = [(lg, lg.disabled, lg.handlers[:], lg.level, lg.propagate) for lg in loggers]
    prev_showwarning = warnings.showwarning
    prev_excepthook, prev_thread_hook = sys.excepthook, threading.excepthook
    structlog.reset_defaults()
    yield
    sys.excepthook, threading.excepthook = prev_excepthook, prev_thread_hook
    logging.captureWarnings(False)
    warnings.showwarning = prev_showwarning
    structlog.reset_defaults()
    conhecidos = {id(lg) for lg, *_ in snapshot}
    for lg in logging.Logger.manager.loggerDict.values():
        # loggers criados durante o teste: voltam ao estado neutro
        if isinstance(lg, logging.Logger) and id(lg) not in conhecidos:
            lg.disabled, lg.handlers, lg.propagate = False, [], True
            lg.setLevel(logging.NOTSET)
    for lg, disabled, handlers, level, propagate in snapshot:
        lg.disabled, lg.handlers, lg.propagate = disabled, handlers, propagate
        lg.setLevel(level)


ConfiguraSap = Callable[..., None]


@pytest.fixture
def sap_env(
    monkeypatch: pytest.MonkeyPatch,
    isolated_settings_env: Path,
) -> ConfiguraSap:
    """Configura env do worker (SAP + banco) para um teste.

    ``creds_via='env'`` (default): SAP_USER/SAP_PASS/DATABASE_URL via variavel de
    ambiente (permitido em dev). ``creds_via='file'``: grava os secrets em
    ``secrets_dir`` (obrigatorio em qas/prd).
    """
    secrets_dir = isolated_settings_env

    def _configure(
        *,
        app_env: str = "dev",
        base_url: str = "https://s4-dev.acme/path/",
        sap_client: str = "300",
        prd_hosts: str | list[str] = "s4-prd.acme",
        creds_via: str = "env",
        sap_user: str = "user1",
        sap_pass: str = "pass1",
        database_url: str = "postgresql+asyncpg://app:db-pass@db:5432/contratos",
    ) -> None:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("SAP_BASE_URL", base_url)
        monkeypatch.setenv("SAP_CLIENT", sap_client)
        prd_hosts_csv = ",".join(prd_hosts) if isinstance(prd_hosts, list) else prd_hosts
        monkeypatch.setenv("SAP_PRD_HOSTS", prd_hosts_csv)

        if creds_via == "env":
            monkeypatch.setenv("SAP_USER", sap_user)
            monkeypatch.setenv("SAP_PASS", sap_pass)
            monkeypatch.setenv("DATABASE_URL", database_url)
        elif creds_via == "file":
            (secrets_dir / "sap_user").write_text(sap_user, encoding="utf-8")
            (secrets_dir / "sap_pass").write_text(sap_pass, encoding="utf-8")
            (secrets_dir / "database_url").write_text(database_url, encoding="utf-8")
        else:
            raise ValueError(f"creds_via desconhecido: {creds_via!r}")

    return _configure
