"""Configuracoes do backend — fail-closed em APP_ENV x SAP host.

Contrato (ver ``docs/plans/fase-0-1.md`` Tarefa 0.3):

- ``SAP_BASE_URL`` e obrigatorio e SEMPRE https (Basic Auth nao trafega em http).
- ``SAP_PRD_HOSTS`` e obrigatorio (lista CSV de hostnames em minusculo, sem
  esquema, sem porta). Ausente/vazio => falha.
- Guard ``APP_ENV`` x host de ``SAP_BASE_URL`` (comparacao case-insensitive,
  igualdade exata — nada de substring/endswith):
    * ``APP_ENV=prd`` exige host ``in SAP_PRD_HOSTS``
    * ``APP_ENV != 'prd'`` exige host ``not in SAP_PRD_HOSTS``
- Credenciais (``SAP_USER`` / ``SAP_PASS``) sao ``SecretStr`` e sao lidas de
  ``secrets_dir`` (default ``/run/secrets``, uma chave por arquivo), que tem
  precedencia sobre env. Fallback de env so em ``dev`` — em ``qas``/``prd`` os
  arquivos precisam existir ou o app nao sobe. ``str()`` / ``repr()`` /
  ``model_dump()`` do Settings nunca expoem a senha em texto.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_SECRETS_DIR_PADRAO = "/run/secrets"


def _secrets_dir_existe(fonte: PydanticBaseSettingsSource) -> bool:
    """True se algum diretorio configurado na fonte de secrets existe."""
    configurado = getattr(fonte, "secrets_dir", None)
    if configurado is None:
        return False
    dirs = [configurado] if isinstance(configurado, (str, Path)) else list(configurado)
    return any(Path(d).is_dir() for d in dirs)


class Settings(BaseSettings):
    """Configuracao central do backend."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        secrets_dir=_SECRETS_DIR_PADRAO,
        extra="ignore",
        # ValidationError nao carrega input_value: erros de nivel de modelo
        # levariam o dict inteiro do env (inclusive SAP_PASS) para o log.
        hide_input_in_errors=True,
    )

    app_env: Literal["dev", "qas", "prd"]
    sap_base_url: HttpUrl
    sap_client: str
    sap_prd_hosts: Annotated[tuple[str, ...], NoDecode]
    sap_user: SecretStr
    sap_pass: SecretStr
    sap_timeout_connect_s: float = 5.0
    sap_timeout_read_s: float = 90.0
    log_level: str = "INFO"

    # -- Fontes ----------------------------------------------------------------

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Arquivo de secret vence env; a fonte so entra se o diretorio existir.

        Avaliado a cada instanciacao (nao no import). Sem o diretorio, a fonte e
        omitida para o pydantic-settings nao emitir ``UserWarning`` em texto puro
        (quebraria o contrato de log 100% JSON). O fail-closed de qas/prd continua
        no validator ``_credenciais_fora_de_dev_devem_vir_de_arquivo``.
        """
        if _secrets_dir_existe(file_secret_settings):
            return init_settings, file_secret_settings, env_settings, dotenv_settings
        return init_settings, env_settings, dotenv_settings

    # -- Validators ----------------------------------------------------------

    @field_validator("sap_prd_hosts", mode="before")
    @classmethod
    def _parse_prd_hosts(cls, valor: object) -> object:
        """Converte CSV do env em tuple de hostnames validos e normalizados."""
        if isinstance(valor, str):
            itens = [x.strip() for x in valor.split(",")]
        elif isinstance(valor, (list, tuple)):
            itens = [str(x).strip() for x in valor]
        else:
            return valor

        limpos: list[str] = []
        for item in itens:
            if not item:
                continue
            if "://" in item:
                raise ValueError(
                    f"SAP_PRD_HOSTS: entrada '{item}' contem esquema; use apenas hostname"
                )
            if ":" in item:
                raise ValueError(
                    f"SAP_PRD_HOSTS: entrada '{item}' contem porta; use apenas hostname"
                )
            limpos.append(item.lower())
        return tuple(limpos)

    @field_validator("sap_prd_hosts", mode="after")
    @classmethod
    def _prd_hosts_nao_vazio(cls, valor: tuple[str, ...]) -> tuple[str, ...]:
        if not valor:
            raise ValueError("SAP_PRD_HOSTS e obrigatorio e nao pode estar vazio")
        return valor

    @field_validator("sap_base_url", mode="after")
    @classmethod
    def _base_url_deve_ser_https(cls, valor: HttpUrl) -> HttpUrl:
        if valor.scheme != "https":
            raise ValueError("SAP_BASE_URL deve usar https")
        return valor

    @model_validator(mode="after")
    def _valida_ambiente_vs_host(self) -> Settings:
        host = (self.sap_base_url.host or "").lower()
        prd_hosts = set(self.sap_prd_hosts)
        if self.app_env == "prd":
            if host not in prd_hosts:
                raise ValueError(
                    "APP_ENV=prd exige SAP_BASE_URL apontando para host em SAP_PRD_HOSTS"
                )
        elif host in prd_hosts:
            raise ValueError(
                f"APP_ENV='{self.app_env}' nao pode apontar para host de "
                "producao listado em SAP_PRD_HOSTS"
            )
        return self

    @model_validator(mode="after")
    def _credenciais_fora_de_dev_devem_vir_de_arquivo(self) -> Settings:
        """Em qas/prd, SAP_USER e SAP_PASS precisam vir de ``secrets_dir``.

        O checklist e ``file.is_file()`` — se o operador esqueceu de montar o
        secret, o app nao sobe, mesmo que os valores existam no env.
        """
        if self.app_env == "dev":
            return self
        secrets_dir_cfg = self.model_config.get("secrets_dir") or _SECRETS_DIR_PADRAO
        secrets_dir = Path(str(secrets_dir_cfg))
        for campo in ("sap_user", "sap_pass"):
            if not (secrets_dir / campo).is_file():
                raise ValueError(
                    f"Em app_env='{self.app_env}', {campo.upper()} deve vir de "
                    f"arquivo em {secrets_dir}, nunca de variavel de ambiente"
                )
        return self
