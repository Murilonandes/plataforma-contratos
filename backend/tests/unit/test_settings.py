"""Testes de ``app.settings.Settings`` (contrato fail-closed).

Cobre: obrigatoriedade, https-only, parsing de SAP_PRD_HOSTS (lowercase, sem
esquema, sem porta), mascaramento de SecretStr e politica de credenciais fora
de dev.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings
from tests.conftest import ConfiguraSap

# ---- Happy path ------------------------------------------------------------


def test_dev_com_env_padrao_carrega(sap_env: ConfiguraSap) -> None:
    sap_env()
    settings = Settings()
    assert settings.app_env == "dev"
    assert str(settings.sap_base_url).startswith("https://s4-dev.acme/")
    assert settings.sap_client == "300"
    assert settings.sap_prd_hosts == ("s4-prd.acme",)
    assert settings.log_level == "INFO"


# ---- Obrigatoriedade e formato ---------------------------------------------


def test_sap_base_url_ausente_falha(sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch) -> None:
    sap_env()
    monkeypatch.delenv("SAP_BASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_sap_prd_hosts_ausente_falha(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch
) -> None:
    sap_env()
    monkeypatch.delenv("SAP_PRD_HOSTS", raising=False)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("valor", ["", "   ", ",", " , , "])
def test_sap_prd_hosts_vazio_ou_so_separador_falha(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch, valor: str
) -> None:
    sap_env()
    monkeypatch.setenv("SAP_PRD_HOSTS", valor)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_PRD_HOSTS e obrigatorio e nao pode estar vazio" in str(exc.value)


def test_sap_base_url_http_e_rejeitado(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch
) -> None:
    sap_env()
    monkeypatch.setenv("SAP_BASE_URL", "http://s4-dev.acme/path/")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_BASE_URL deve usar https" in str(exc.value)


def test_prd_hosts_com_esquema_falha(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch
) -> None:
    sap_env()
    monkeypatch.setenv("SAP_PRD_HOSTS", "https://s4-prd.acme")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_PRD_HOSTS" in str(exc.value)
    assert "esquema" in str(exc.value)


def test_prd_hosts_com_porta_falha(sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch) -> None:
    sap_env()
    monkeypatch.setenv("SAP_PRD_HOSTS", "s4-prd.acme:443")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_PRD_HOSTS" in str(exc.value)
    assert "porta" in str(exc.value)


def test_prd_hosts_lowercase_normalizado(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch
) -> None:
    sap_env()
    monkeypatch.setenv("SAP_PRD_HOSTS", "S4-PRD.ACME , S4-PRD-DR.ACME")
    settings = Settings()
    assert settings.sap_prd_hosts == ("s4-prd.acme", "s4-prd-dr.acme")


# ---- SecretStr / mascaramento ---------------------------------------------


def test_credenciais_via_env_em_dev_ok(sap_env: ConfiguraSap) -> None:
    sap_env(sap_user="usr-x", sap_pass="pw-x")
    settings = Settings()
    assert settings.sap_user.get_secret_value() == "usr-x"
    assert settings.sap_pass.get_secret_value() == "pw-x"


def test_str_do_settings_nao_expoe_senha(sap_env: ConfiguraSap) -> None:
    senha = "super-secret-9f8e7d6c5b4a"
    sap_env(sap_pass=senha)
    settings = Settings()
    assert senha not in str(settings)
    assert senha not in repr(settings)


def test_model_dump_nao_expoe_senha(sap_env: ConfiguraSap) -> None:
    senha = "outro-segredo-01234"
    sap_env(sap_pass=senha)
    settings = Settings()
    dump = settings.model_dump()
    assert senha not in repr(dump)
    dump_json = settings.model_dump(mode="json")
    assert dump_json["sap_pass"] == "**********"
    assert senha not in repr(dump_json)


def test_credenciais_via_file_em_dev_tambem_ok(sap_env: ConfiguraSap) -> None:
    sap_env(creds_via="file", sap_user="from-file", sap_pass="pw-from-file")
    settings = Settings()
    assert settings.sap_user.get_secret_value() == "from-file"
    assert settings.sap_pass.get_secret_value() == "pw-from-file"


# ---- Politica de credenciais fora de dev ----------------------------------


@pytest.mark.parametrize("app_env", ["qas", "prd"])
def test_fora_de_dev_credenciais_devem_vir_de_arquivo(sap_env: ConfiguraSap, app_env: str) -> None:
    # Para prd o host tem que estar na lista; para qas nao pode estar.
    if app_env == "prd":
        sap_env(
            app_env=app_env,
            base_url="https://s4-prd.acme/path/",
            prd_hosts="s4-prd.acme",
            creds_via="env",
        )
    else:
        sap_env(app_env=app_env, creds_via="env")

    with pytest.raises(ValidationError) as exc:
        Settings()
    msg = str(exc.value)
    assert app_env in msg
    assert "SAP_USER" in msg or "SAP_PASS" in msg


def test_prd_com_credenciais_de_arquivo_carrega(sap_env: ConfiguraSap) -> None:
    sap_env(
        app_env="prd",
        base_url="https://s4-prd.acme/path/",
        prd_hosts="s4-prd.acme",
        creds_via="file",
        sap_user="prd-user",
        sap_pass="prd-pass",
    )
    settings = Settings()
    assert settings.app_env == "prd"
    assert settings.sap_user.get_secret_value() == "prd-user"
    assert settings.sap_pass.get_secret_value() == "prd-pass"
