"""Hardening do Settings apos a revisao de seguranca da Fase 0.

- Achado 1: SAP_PRD_HOSTS e host da SAP_BASE_URL normalizados pela MESMA funcao
  (lowercase, IDNA/punycode, sem ponto final); entrada invalida derruba o boot.
- Achado 4: conteudo de secret com strip; vazio em qas/prd derruba o boot.
- Achado 5: em qas/prd a credencial vem da fonte de arquivo efetiva (sem init
  kwargs, sem _secrets_dir diferente do configurado).
- Achado 6: SAP_BASE_URL com userinfo derruba o boot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import Settings, normalizar_host
from tests.conftest import ConfiguraSap

_PRD = {"app_env": "prd", "base_url": "https://s4-prd.acme/x/", "prd_hosts": "s4-prd.acme"}


# ---- normalizar_host ---------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("S4-PRD.Acme.COM.br", "s4-prd.acme.com.br"),
        ("s4-prd.acme.com.br.", "s4-prd.acme.com.br"),
        ("s4-prd-acmé.com", "xn--s4-prd-acm-k7a.com"),
        ("s4-prd\uff0eacme\uff0ecom", "s4-prd.acme.com"),  # ponto fullwidth
        ("10.0.0.5", "10.0.0.5"),
        ("FD00:0:0::5", "fd00::5"),
    ],
)
def test_normalizar_host_valido(entrada: str, esperado: str) -> None:
    assert normalizar_host(entrada) == esperado


@pytest.mark.parametrize(
    "entrada",
    ["", "host/", "https//host", '["host"]', "host;outro", "ho st", "[fd00::5]", "host..", "-a.b"],
)
def test_normalizar_host_invalido(entrada: str) -> None:
    with pytest.raises(ValueError, match="hostname"):
        normalizar_host(entrada)


# ---- SAP_PRD_HOSTS: formatos reproduzidos pelo revisor -----------------------


@pytest.mark.parametrize(
    "prd_hosts",
    [
        "s4-prd.acme/",
        "https//s4-prd.acme",
        '["s4-prd.acme"]',
        "s4-prd.acme;outro",
        " , ,s4-dev.acme",  # entrada vazia no meio
        "s4-prd .acme",
    ],
)
def test_prd_hosts_malformado_derruba_o_boot(sap_env: ConfiguraSap, prd_hosts: str) -> None:
    sap_env(prd_hosts=prd_hosts)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_PRD_HOSTS" in str(exc.value)


@pytest.mark.parametrize(
    ("base_url", "prd_hosts"),
    [
        ("https://s4-prd.acme/x/", "s4-prd.acme."),  # ponto final na lista
        ("https://s4-prd.acme./x/", "s4-prd.acme"),  # ponto final na URL
        ("https://s4-prd-acmé.com/x/", "s4-prd-acmé.com"),  # IDN nos dois lados
        ("https://xn--s4-prd-acm-k7a.com/x/", "s4-prd-acmé.com"),  # punycode x unicode
        ("https://10.0.0.5/x/", "10.0.0.5"),  # IPv4 literal
        ("https://[fd00::5]/x/", "fd00:0::5"),  # IPv6 literal
    ],
)
def test_guard_bate_apos_normalizacao_e_bloqueia_dev(
    sap_env: ConfiguraSap, base_url: str, prd_hosts: str
) -> None:
    sap_env(app_env="dev", base_url=base_url, prd_hosts=prd_hosts)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "nao pode apontar para host de producao" in str(exc.value)


def test_prd_hosts_guarda_forma_normalizada(sap_env: ConfiguraSap) -> None:
    sap_env(prd_hosts="S4-PRD.ACME., s4-prd-acmé.com, 10.0.0.5, fd00:0::5")
    assert Settings().sap_prd_hosts == (
        "s4-prd.acme",
        "xn--s4-prd-acm-k7a.com",
        "10.0.0.5",
        "fd00::5",
    )


# ---- Achado 6: userinfo na URL ----------------------------------------------


@pytest.mark.parametrize(
    "base_url", ["https://svc:TopSecret@s4-dev.acme/x/", "https://svc@s4-dev.acme/x/"]
)
def test_base_url_com_userinfo_derruba_o_boot(sap_env: ConfiguraSap, base_url: str) -> None:
    sap_env(base_url=base_url)
    with pytest.raises(ValidationError) as exc:
        Settings()
    msg = str(exc.value)
    assert "SAP_BASE_URL nao pode conter usuario/senha" in msg
    assert "TopSecret" not in msg


# ---- Achado 4: conteudo dos secrets -----------------------------------------


def test_secret_de_arquivo_com_newline_final_e_stripado(
    sap_env: ConfiguraSap, isolated_settings_env: Path
) -> None:
    sap_env(**_PRD, creds_via="file")
    (isolated_settings_env / "sap_user").write_text("usuario\n", encoding="utf-8")
    (isolated_settings_env / "sap_pass").write_text("senha\n", encoding="utf-8")
    settings = Settings()
    assert settings.sap_user.get_secret_value() == "usuario"
    assert settings.sap_pass.get_secret_value() == "senha"


@pytest.mark.parametrize("app_env", ["qas", "prd"])
@pytest.mark.parametrize("conteudo", ["", "   ", "\n", " \t\n"])
def test_secret_vazio_fora_de_dev_derruba_o_boot(
    sap_env: ConfiguraSap,
    isolated_settings_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
    conteudo: str,
) -> None:
    extra = _PRD if app_env == "prd" else {"app_env": "qas"}
    sap_env(**extra, creds_via="file")
    (isolated_settings_env / "sap_pass").write_text(conteudo, encoding="utf-8")
    monkeypatch.setenv("SAP_PASS", "do-env-nao-pode-salvar")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_PASS" in str(exc.value)
    assert "vazio" in str(exc.value)


def test_credencial_vazia_em_dev_continua_aceita(sap_env: ConfiguraSap) -> None:
    sap_env(sap_user="", sap_pass="")
    assert Settings().sap_pass.get_secret_value() == ""


# ---- Achado 5: fonte efetiva em qas/prd -------------------------------------


@pytest.mark.parametrize("campo", ["sap_user", "sap_pass"])
def test_prd_rejeita_credencial_por_init_kwarg(sap_env: ConfiguraSap, campo: str) -> None:
    sap_env(**_PRD, creds_via="file")
    kwargs = {campo: "via-kwarg"}
    with pytest.raises(ValidationError) as exc:
        Settings(**kwargs)  # type: ignore[arg-type]
    assert campo.upper() in str(exc.value)


def test_prd_rejeita_secrets_dir_diferente_do_configurado(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sap_env(**_PRD, creds_via="file")  # arquivos no diretorio configurado
    outro = tmp_path / "outro"
    outro.mkdir()
    monkeypatch.setenv("SAP_USER", "u-env")
    monkeypatch.setenv("SAP_PASS", "p-env")
    with pytest.raises(ValidationError) as exc:
        Settings(_secrets_dir=str(outro))  # type: ignore[call-arg]
    assert "secrets_dir" in str(exc.value)


def test_dev_aceita_init_kwargs(sap_env: ConfiguraSap) -> None:
    sap_env()
    assert Settings(sap_pass="kw").sap_pass.get_secret_value() == "kw"  # type: ignore[arg-type]


def test_credencial_de_env_tambem_e_stripada_em_dev(
    sap_env: ConfiguraSap, monkeypatch: pytest.MonkeyPatch
) -> None:
    sap_env()
    monkeypatch.setenv("SAP_PASS", "  senha\n")
    assert Settings().sap_pass.get_secret_value() == "senha"


def test_prd_rejeita_init_kwarg_mesmo_com_valor_igual_ao_do_arquivo(
    sap_env: ConfiguraSap,
) -> None:
    sap_env(**_PRD, creds_via="file", sap_pass="p-arquivo")
    with pytest.raises(ValidationError) as exc:
        Settings(sap_pass="p-arquivo")  # type: ignore[arg-type]
    assert "SAP_PASS nao pode vir de argumento" in str(exc.value)


def test_prd_rejeita_secrets_dir_alternativo_mesmo_com_arquivos_identicos(
    sap_env: ConfiguraSap, tmp_path: Path
) -> None:
    sap_env(**_PRD, creds_via="file", sap_user="u", sap_pass="p")
    outro = tmp_path / "outro"
    outro.mkdir()
    (outro / "sap_user").write_text("u", encoding="utf-8")
    (outro / "sap_pass").write_text("p", encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        Settings(_secrets_dir=str(outro))  # type: ignore[call-arg]
    assert "secrets_dir efetivo difere do configurado" in str(exc.value)


@pytest.mark.parametrize(
    "prd_hosts", [" , ,s4-dev.acme", "s4-prd.acme,,s4-dr.acme", "s4-prd.acme,"]
)
def test_prd_hosts_entrada_vazia_tem_mensagem_clara(sap_env: ConfiguraSap, prd_hosts: str) -> None:
    sap_env(prd_hosts=prd_hosts)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_PRD_HOSTS: entrada vazia na lista" in str(exc.value)
