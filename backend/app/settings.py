"""Configuracoes do backend — fail-closed em APP_ENV x SAP host.

Contrato (ver ``docs/plans/fase-0-1.md`` Tarefa 0.3 e a revisao de seguranca):

- ``SAP_BASE_URL`` e obrigatorio, SEMPRE https (Basic Auth nao trafega em http)
  e sem userinfo (``user:pass@``): credencial so vem de SAP_USER/SAP_PASS.
- ``SAP_PRD_HOSTS`` e obrigatorio: lista CSV em que cada entrada e hostname
  valido ou IP literal. Qualquer entrada invalida (``/``, ``;``, ``[``, espaco
  interno, vazia, esquema, porta) derruba o boot.
- Guard ``APP_ENV`` x host: os DOIS lados passam por ``normalizar_host``
  (lowercase, IDNA/punycode, sem ponto final) e comparam por igualdade exata.
    * ``APP_ENV=prd`` exige host ``in SAP_PRD_HOSTS``
    * ``APP_ENV != 'prd'`` exige host ``not in SAP_PRD_HOSTS``
- Credenciais (``SAP_USER`` / ``SAP_PASS``) sao ``SecretStr`` com strip. Vem de
  ``secrets_dir`` (default ``/run/secrets``, um arquivo por chave), que tem
  precedencia sobre env. Em ``dev`` o env e aceito. Em ``qas``/``prd`` so a
  fonte de arquivo EFETIVA vale: sem init kwargs, sem ``_secrets_dir`` diferente
  do configurado, arquivo existente e nao vazio, e valor igual ao do arquivo.
- ``str()`` / ``repr()`` / ``model_dump()`` / ``ValidationError`` nunca expoem a
  senha (``hide_input_in_errors``).
"""

from __future__ import annotations

import ipaddress
import re
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_SECRETS_DIR_PADRAO = "/run/secrets"
_CAMPOS_CREDENCIAL = ("sap_user", "sap_pass")
_LABEL_HOSTNAME = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalizar_host(valor: str) -> str:
    """Forma canonica de um host para comparacao: hostname ou IP literal.

    Hostname: lowercase, IDNA (punycode), sem o ponto final de FQDN; cada label
    com [a-z0-9-], 1..63 chars, sem hifen nas pontas; total ate 253. IP: forma
    comprimida do ``ipaddress``. Qualquer outra coisa -> ``ValueError``.
    """
    try:
        return str(ipaddress.ip_address(valor))
    except ValueError:
        pass
    candidato = valor.lower()
    if candidato.endswith((".", "\uff0e", "\u3002", "\uff61")):
        candidato = candidato[:-1]
    try:
        ascii_host = candidato.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("nao e hostname valido nem IP literal") from exc
    labels = ascii_host.split(".")
    if not ascii_host or len(ascii_host) > 253 or not all(_LABEL_HOSTNAME.match(x) for x in labels):
        raise ValueError("nao e hostname valido nem IP literal")
    return ascii_host


@dataclass(frozen=True)
class _FontesCredencial:
    """O que a instanciacao corrente usou de fato (preenchido nas fontes)."""

    secrets_dir_efetivo: Path | None
    credenciais_por_init: frozenset[str]


# settings_customise_sources roda dentro do __init__ de cada instancia, antes dos
# validators; o validator de credenciais le daqui a fonte efetiva.
_fontes_correntes: ContextVar[_FontesCredencial | None] = ContextVar(
    "_fontes_correntes", default=None
)


def _dir_configurado(config: SettingsConfigDict) -> Path:
    return Path(str(config.get("secrets_dir") or _SECRETS_DIR_PADRAO))


def _dir_efetivo(fonte: PydanticBaseSettingsSource) -> Path | None:
    """Diretorio que a fonte de secrets vai ler de fato (None se nao existe)."""
    configurado = getattr(fonte, "secrets_dir", None)
    if configurado is None:
        return None
    dirs = [configurado] if isinstance(configurado, (str, Path)) else list(configurado)
    existentes = [Path(d) for d in dirs if Path(d).is_dir()]
    if len(existentes) != 1:
        return None if not existentes else Path("<multiplos>")
    return existentes[0]


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
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

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
        omitida para o pydantic-settings nao emitir ``UserWarning``. Registra a
        fonte efetiva para o validator de credenciais de qas/prd.
        """
        init_kwargs = (
            init_settings.init_kwargs if isinstance(init_settings, InitSettingsSource) else {}
        )
        efetivo = _dir_efetivo(file_secret_settings)
        _fontes_correntes.set(
            _FontesCredencial(
                secrets_dir_efetivo=efetivo,
                credenciais_por_init=frozenset(
                    k.lower() for k in init_kwargs if k.lower() in _CAMPOS_CREDENCIAL
                ),
            )
        )
        if efetivo is not None:
            return init_settings, file_secret_settings, env_settings, dotenv_settings
        return init_settings, env_settings, dotenv_settings

    # -- Validators de campo ---------------------------------------------------

    @field_validator("sap_prd_hosts", mode="before")
    @classmethod
    def _parse_prd_hosts(cls, valor: object) -> object:
        """CSV -> tuple de hosts normalizados; entrada invalida derruba o boot."""
        if isinstance(valor, str):
            itens = [x.strip() for x in valor.split(",")]
        elif isinstance(valor, (list, tuple)):
            itens = [str(x).strip() for x in valor]
        else:
            return valor
        if not any(itens):
            return ()  # tratado como "obrigatorio" no validator after

        normalizados: list[str] = []
        for item in itens:
            if not item:
                raise ValueError("SAP_PRD_HOSTS: entrada vazia na lista (virgula sobrando?)")
            if "://" in item:
                raise ValueError(
                    f"SAP_PRD_HOSTS: entrada '{item}' contem esquema; use apenas hostname"
                )
            try:
                normalizados.append(normalizar_host(item))
            except ValueError:
                if ":" in item and "[" not in item:
                    raise ValueError(
                        f"SAP_PRD_HOSTS: entrada '{item}' contem porta; use apenas hostname"
                    ) from None
                raise ValueError(
                    f"SAP_PRD_HOSTS: entrada '{item}' nao e hostname valido nem IP literal"
                ) from None
        return tuple(normalizados)

    @field_validator("sap_prd_hosts", mode="after")
    @classmethod
    def _prd_hosts_nao_vazio(cls, valor: tuple[str, ...]) -> tuple[str, ...]:
        if not valor:
            raise ValueError("SAP_PRD_HOSTS e obrigatorio e nao pode estar vazio")
        return valor

    @field_validator("sap_base_url", mode="after")
    @classmethod
    def _base_url_segura(cls, valor: HttpUrl) -> HttpUrl:
        if valor.scheme != "https":
            raise ValueError("SAP_BASE_URL deve usar https")
        if valor.username is not None or valor.password is not None:
            raise ValueError(
                "SAP_BASE_URL nao pode conter usuario/senha na URL; use SAP_USER/SAP_PASS (secret)"
            )
        return valor

    @field_validator("sap_user", "sap_pass", mode="before")
    @classmethod
    def _strip_credencial(cls, valor: object) -> object:
        """Remove espacos e o \\n final de arquivo criado com ``echo``."""
        if isinstance(valor, SecretStr):
            return SecretStr(valor.get_secret_value().strip())
        if isinstance(valor, str):
            return valor.strip()
        return valor

    @field_validator("log_level", mode="before")
    @classmethod
    def _normaliza_log_level(cls, valor: object) -> object:
        return valor.strip().upper() if isinstance(valor, str) else valor

    # -- Validators de modelo --------------------------------------------------

    @model_validator(mode="after")
    def _valida_ambiente_vs_host(self) -> Settings:
        host_url = (self.sap_base_url.host or "").removeprefix("[").removesuffix("]")
        try:
            host = normalizar_host(host_url)
        except ValueError:
            raise ValueError("SAP_BASE_URL: host nao e hostname valido nem IP literal") from None
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
        """Em qas/prd, SAP_USER e SAP_PASS so valem vindos da fonte de arquivo efetiva.

        Confere: nenhum init kwarg de credencial; o diretorio lido e o
        configurado; o arquivo existe, nao e vazio apos strip e o valor carregado
        e o do arquivo (se o operador esqueceu o secret, o env nao salva o boot).
        """
        if self.app_env == "dev":
            return self
        fontes = _fontes_correntes.get()
        configurado = _dir_configurado(self.model_config)
        for campo in _CAMPOS_CREDENCIAL:
            nome = campo.upper()
            if fontes is not None and campo in fontes.credenciais_por_init:
                raise ValueError(
                    f"Em app_env='{self.app_env}', {nome} nao pode vir de argumento; "
                    f"use arquivo em {configurado}"
                )
            if fontes is None or fontes.secrets_dir_efetivo is None:
                raise ValueError(
                    f"Em app_env='{self.app_env}', {nome} deve vir de arquivo em "
                    f"{configurado}, nunca de variavel de ambiente"
                )
            if fontes.secrets_dir_efetivo.resolve() != configurado.resolve():
                raise ValueError(
                    f"Em app_env='{self.app_env}', secrets_dir efetivo difere do configurado "
                    f"({configurado})"
                )
            arquivo = configurado / campo
            if not arquivo.is_file():
                raise ValueError(
                    f"Em app_env='{self.app_env}', {nome} deve vir de arquivo em "
                    f"{configurado}, nunca de variavel de ambiente"
                )
            conteudo = arquivo.read_text(encoding="utf-8").strip()
            if not conteudo:
                raise ValueError(
                    f"Em app_env='{self.app_env}', arquivo de {nome} em {configurado} esta vazio"
                )
            # Defesa em profundidade: com arquivo > env e init barrado, so difere se
            # a fonte (case-insensitive) leu outro arquivo, ex. SAP_PASS vs sap_pass
            # num filesystem case-sensitive.
            if getattr(self, campo).get_secret_value() != conteudo:
                raise ValueError(
                    f"Em app_env='{self.app_env}', {nome} carregado nao veio do arquivo em "
                    f"{configurado}"
                )
        return self
