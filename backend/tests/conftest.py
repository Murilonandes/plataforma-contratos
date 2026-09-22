"""Fixtures compartilhadas de teste.

Isola o ambiente de cada teste: aponta o ``secrets_dir`` do ``Settings`` para
um diretorio temporario (evita depender de ``/run/secrets`` na maquina do dev)
e limpa qualquer ``SAP_*`` / ``APP_ENV`` / ``LOG_LEVEL`` herdado do shell.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

_ENV_VARS_ISOLADAS = (
    "APP_ENV",
    "SAP_BASE_URL",
    "SAP_CLIENT",
    "SAP_PRD_HOSTS",
    "SAP_USER",
    "SAP_PASS",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def isolated_settings_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Sanitiza env e redireciona ``secrets_dir`` do Settings pra ``tmp_path``."""
    from app.settings import Settings

    secrets_dir = tmp_path / "run_secrets"
    secrets_dir.mkdir()
    monkeypatch.setitem(Settings.model_config, "secrets_dir", str(secrets_dir))

    for var in _ENV_VARS_ISOLADAS:
        monkeypatch.delenv(var, raising=False)

    return secrets_dir


ConfiguraSap = Callable[..., None]


@pytest.fixture
def sap_env(
    monkeypatch: pytest.MonkeyPatch,
    isolated_settings_env: Path,
) -> ConfiguraSap:
    """Configura env do SAP para um teste.

    ``creds_via='env'`` (default): SAP_USER/SAP_PASS via variavel de ambiente
    (permitido em dev). ``creds_via='file'``: grava os secrets em ``secrets_dir``
    (obrigatorio em qas/prd).
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
    ) -> None:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("SAP_BASE_URL", base_url)
        monkeypatch.setenv("SAP_CLIENT", sap_client)
        prd_hosts_csv = ",".join(prd_hosts) if isinstance(prd_hosts, list) else prd_hosts
        monkeypatch.setenv("SAP_PRD_HOSTS", prd_hosts_csv)

        if creds_via == "env":
            monkeypatch.setenv("SAP_USER", sap_user)
            monkeypatch.setenv("SAP_PASS", sap_pass)
        elif creds_via == "file":
            (secrets_dir / "sap_user").write_text(sap_user, encoding="utf-8")
            (secrets_dir / "sap_pass").write_text(sap_pass, encoding="utf-8")
        else:
            raise ValueError(f"creds_via desconhecido: {creds_via!r}")

    return _configure
