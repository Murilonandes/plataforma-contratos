"""Matriz APP_ENV x SAP_BASE_URL x SAP_PRD_HOSTS (guard fail-closed)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings
from tests.conftest import ConfiguraSap

# Cada caso: (id, app_env, url_base, prd_hosts, deve_carregar, msg_parcial)
Case = tuple[str, str, str, str, bool, str | None]

_CASOS_MATRIZ: list[Case] = [
    ("1_prd_host_bate_lista_unica", "prd", "https://sap-prd.acme/x/", "sap-prd.acme", True, None),
    (
        "2_prd_host_bate_lista_multipla",
        "prd",
        "https://sap-prd.acme/x/",
        "sap-prd.acme,sap-prd-dr.acme",
        True,
        None,
    ),
    (
        "3_prd_host_nao_bate",
        "prd",
        "https://sap-outro.acme/x/",
        "sap-prd.acme",
        False,
        "APP_ENV=prd exige SAP_BASE_URL apontando para host em SAP_PRD_HOSTS",
    ),
    ("4_dev_host_nao_prd", "dev", "https://sap-dev.acme/x/", "sap-prd.acme", True, None),
    (
        "5_dev_apontando_pra_prd",
        "dev",
        "https://sap-prd.acme/x/",
        "sap-prd.acme",
        False,
        "nao pode apontar para host de producao",
    ),
    ("6_qas_host_nao_prd", "qas", "https://sap-qas.acme/x/", "sap-prd.acme", True, None),
    (
        "7_qas_apontando_pra_prd",
        "qas",
        "https://sap-prd.acme/x/",
        "sap-prd.acme",
        False,
        "nao pode apontar para host de producao",
    ),
    (
        "8_prd_lista_vazia",
        "prd",
        "https://sap-prd.acme/x/",
        "",
        False,
        "SAP_PRD_HOSTS e obrigatorio e nao pode estar vazio",
    ),
    (
        "9_dev_lista_vazia",
        "dev",
        "https://sap-dev.acme/x/",
        "",
        False,
        "SAP_PRD_HOSTS e obrigatorio e nao pode estar vazio",
    ),
]


@pytest.mark.parametrize(
    ("app_env", "base_url", "prd_hosts_csv", "deve_carregar", "msg_parcial"),
    [c[1:] for c in _CASOS_MATRIZ],
    ids=[c[0] for c in _CASOS_MATRIZ],
)
def test_matriz_env_x_host(
    sap_env: ConfiguraSap,
    app_env: str,
    base_url: str,
    prd_hosts_csv: str,
    deve_carregar: bool,
    msg_parcial: str | None,
) -> None:
    creds_via = "file" if app_env in ("qas", "prd") else "env"
    sap_env(
        app_env=app_env,
        base_url=base_url,
        prd_hosts=prd_hosts_csv,
        creds_via=creds_via,
    )
    if deve_carregar:
        settings = Settings()
        assert settings.app_env == app_env
    else:
        assert msg_parcial is not None
        with pytest.raises(ValidationError) as exc:
            Settings()
        assert msg_parcial in str(exc.value)


# ---- Extras solicitados: case, porta, subdominio parecido, http ------------


def test_prd_url_maiuscula_bate_lista_lowercase(sap_env: ConfiguraSap) -> None:
    """Hostname da URL vem em MAIUSCULA; comparacao e case-insensitive."""
    sap_env(
        app_env="prd",
        base_url="https://S4-PRD.ACME/path/",
        prd_hosts="s4-prd.acme",
        creds_via="file",
    )
    settings = Settings()
    assert settings.app_env == "prd"


def test_prd_url_com_porta_explicita_ok(sap_env: ConfiguraSap) -> None:
    """SAP_BASE_URL pode ter :443; a porta e ignorada na comparacao de host."""
    sap_env(
        app_env="prd",
        base_url="https://s4-prd.acme:443/path/",
        prd_hosts="s4-prd.acme",
        creds_via="file",
    )
    settings = Settings()
    assert settings.sap_base_url.host == "s4-prd.acme"


def test_prd_subdominio_parecido_rejeita(sap_env: ConfiguraSap) -> None:
    """Comparacao por igualdade exata: 's4-prd.acme.evil.com' nao bate 's4-prd.acme'."""
    sap_env(
        app_env="prd",
        base_url="https://s4-prd.acme.evil.com/path/",
        prd_hosts="s4-prd.acme",
        creds_via="file",
    )
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "APP_ENV=prd exige" in str(exc.value)


def test_http_e_sempre_rejeitado(sap_env: ConfiguraSap) -> None:
    """Basic Auth nao pode trafegar em http, em nenhum ambiente."""
    sap_env(base_url="http://sap-dev.acme/path/")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_BASE_URL deve usar https" in str(exc.value)
