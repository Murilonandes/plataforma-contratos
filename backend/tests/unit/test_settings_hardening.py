"""Hardening do Settings apos as revisoes de seguranca da Fase 0.

- Guard: SAP_PRD_HOSTS e o host da SAP_BASE_URL so aceitam hostname DNS ASCII
  (letras, digitos, hifen e pontos; rotulos de 1 a 63; ao menos um ponto).
  IP literal (v4/v6, mapeado, zone id), nao-ASCII e rotulos xn-- sao
  proibidos. Entrada rejeitada aparece truncada em 64 caracteres.
- Achado 4: conteudo de secret com strip; vazio em qas/prd derruba o boot.
- Achado 5: em qas/prd a credencial vem da fonte de arquivo efetiva (sem init
  kwargs, sem _secrets_dir diferente do configurado).
- Achado 6: SAP_BASE_URL com userinfo derruba o boot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import Settings, validar_hostname
from tests.conftest import ConfiguraSap

_PRD = {"app_env": "prd", "base_url": "https://s4-prd.acme/x/", "prd_hosts": "s4-prd.acme"}

# Tudo o que nao e hostname DNS ASCII. Vale para os dois lados do guard.
_NAO_HOSTNAME = [
    "",
    "localhost",  # sem ponto
    "s4-prd.acme.",  # ponto final (rotulo vazio)
    "host..x",
    "-a.b",
    "a-.b",
    ("a" * 64) + ".com",  # rotulo > 63
    ("a." * 127) + "com",  # > 253
    "host/",
    "https//host",
    '["host.acme"]',
    "host.acme;outro",
    "ho st.acme",
    "s4_prd.acme",
    "10.0.0.5",
    "10.0.5",
    "010.0.0.5",
    "0x0a.0.0.5",
    "167772165",
    "fd00::5",
    "[fd00::5]",
    "::ffff:10.0.0.5",
    "::1:443",
    "fe80::1%eth0",
    "s4-prd-acmé.com",  # nao-ASCII
    "straße.de",
    "s4\uff0eacme\uff0ecom",  # ponto fullwidth
    "xn--s4-prd-acm-k7a.com",  # IDN em punycode
    "s4.xn--p1ai",
]


# ---- validar_hostname --------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("S4-PRD.Acme.COM.br", "s4-prd.acme.com.br"),
        ("s4-dev.brfertil.com.br", "s4-dev.brfertil.com.br"),
        ("a-1.b2.co", "a-1.b2.co"),
        ("host.123abc", "host.123abc"),  # ultimo rotulo com letra e ok
    ],
)
def test_validar_hostname_aceita_dns_ascii(entrada: str, esperado: str) -> None:
    assert validar_hostname(entrada) == esperado


@pytest.mark.parametrize("entrada", _NAO_HOSTNAME)
def test_validar_hostname_rejeita(entrada: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 — o motivo varia por caso
        validar_hostname(entrada)


# ---- SAP_PRD_HOSTS -----------------------------------------------------------


@pytest.mark.parametrize("prd_hosts", [h for h in _NAO_HOSTNAME if h])
def test_prd_hosts_invalido_derruba_o_boot(sap_env: ConfiguraSap, prd_hosts: str) -> None:
    sap_env(prd_hosts=prd_hosts)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_PRD_HOSTS: entrada" in str(exc.value)


def test_prd_hosts_entrada_rejeitada_aparece_truncada_em_64(sap_env: ConfiguraSap) -> None:
    entrada = ("a" * 63) + ";" + ("Z" * 200)
    sap_env(prd_hosts=entrada)
    with pytest.raises(ValidationError) as exc:
        Settings()
    msg = str(exc.value)
    assert f"'{entrada[:64]}...'" in msg
    assert "Z" not in msg


def test_prd_hosts_guarda_em_lowercase(sap_env: ConfiguraSap) -> None:
    sap_env(prd_hosts="S4-PRD.ACME, s4-DR.acme")
    assert Settings().sap_prd_hosts == ("s4-prd.acme", "s4-dr.acme")


# ---- host da SAP_BASE_URL ----------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "https://10.0.0.5/x/",
        "https://[::ffff:10.0.0.5]/x/",
        "https://[::ffff:a00:5]/x/",
        "https://[fd00::5]/x/",
        "https://0x0a.0.0.5/x/",  # o pydantic canonicaliza para 10.0.0.5
        "https://s4-prd-acmé.com/x/",  # vira xn-- no pydantic
        "https://xn--s4-prd-acm-k7a.com/x/",
        "https://straße.de/x/",
        "https://s4-prd.acme./x/",
        "https://localhost/x/",
    ],
)
@pytest.mark.parametrize("app_env", ["dev", "prd"])
def test_base_url_com_host_nao_dns_ascii_derruba_o_boot(
    sap_env: ConfiguraSap, base_url: str, app_env: str
) -> None:
    creds = "file" if app_env == "prd" else "env"
    sap_env(app_env=app_env, base_url=base_url, prd_hosts="s4-prd.acme", creds_via=creds)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "SAP_BASE_URL: host precisa ser hostname DNS ASCII" in str(exc.value)


def test_guard_case_insensitive_bloqueia_dev(sap_env: ConfiguraSap) -> None:
    sap_env(app_env="dev", base_url="https://S4-PRD.ACME/x/", prd_hosts="s4-prd.ACME")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "nao pode apontar para host de producao" in str(exc.value)


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
