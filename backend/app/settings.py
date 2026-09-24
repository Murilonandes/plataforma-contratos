"""Configuracoes do backend — fail-closed em APP_ENV x SAP host.

Uma classe por processo (Fase 2, Tarefa 2.1): a API nunca recebe o SAP.

- ``ApiSettings``: ``APP_ENV``, ``LOG_LEVEL`` e ``DATABASE_URL``.
- ``WorkerSettings``: o mesmo mais a config SAP abaixo e a do worker
  (``SAP_DECIMAL_AS_STRING``, ``SAP_MAX_TENTATIVAS``, ``SAP_LOCK_TIMEOUT_S``,
  ``WORKER_POLL_INTERVAL_S``). Guard do lock (D4): ``SAP_LOCK_TIMEOUT_S`` precisa
  ser maior que o prazo por tentativa + 60 s, com prazo = 4 x (connect + read)
  (CSRF + POST + refetch do CSRF + POST, pior caso).
- ``DATABASE_URL`` (``postgresql+asyncpg://``) e credencial como SAP_USER/SAP_PASS:
  a regra de fonte de arquivo abaixo vale para toda credencial da classe.

Contrato do SAP (ver ``docs/plans/fase-0-1.md`` Tarefa 0.3 e a revisao de seguranca):

- ``SAP_BASE_URL`` e obrigatorio, SEMPRE https (Basic Auth nao trafega em http)
  e sem userinfo (``user:pass@``): credencial so vem de SAP_USER/SAP_PASS.
- ``SAP_PRD_HOSTS`` e obrigatorio: lista CSV de hostnames. Qualquer entrada
  invalida (vazia, esquema, porta, ``/``, ``;``, espaco...) derruba o boot; a
  entrada rejeitada aparece na mensagem truncada em 64 caracteres.
- Guard ``APP_ENV`` x host: os DOIS lados passam por ``validar_hostname`` — so
  hostname DNS ASCII (letras, digitos, hifen, pontos; rotulos de 1 a 63; ao
  menos um ponto; ultimo rotulo nao numerico). IP literal (v4/v6, mapeado,
  zone id), nao-ASCII e rotulos ``xn--`` sao proibidos: sem normalizacao nao
  ha divergencia entre os lados. Comparacao por igualdade exata, em lowercase.
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
from typing import Annotated, ClassVar, Literal

from pydantic import (
    Field,
    HttpUrl,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_SECRETS_DIR_PADRAO = "/run/secrets"
_ROTULO_DNS = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_MAX_ECO = 64


def _eh_ip_literal(valor: str) -> bool:
    candidato = valor.removeprefix("[").removesuffix("]").split("%", 1)[0]
    try:
        ipaddress.ip_address(candidato)
    except ValueError:
        return False
    return True


def validar_hostname(valor: str) -> str:
    """Devolve o hostname em lowercase ou levanta ``ValueError`` com o motivo.

    Aceita so hostname DNS ASCII: letras, digitos, hifen e pontos; rotulos de 1
    a 63 sem hifen nas pontas; ao menos um ponto; ate 253 caracteres; ultimo
    rotulo nao numerico (pega formas de IPv4 que o ``ipaddress`` nao reconhece,
    como ``10.0.5`` e ``0x0a.0.0.5``). Proibe IP literal, nao-ASCII e ``xn--``.
    """
    host = valor.lower()
    if not host.isascii():
        raise ValueError("so ASCII (IDN nao e permitido)")
    if _eh_ip_literal(host):
        raise ValueError("IP literal nao e permitido; use o hostname DNS")
    if "." not in host or len(host) > 253:
        raise ValueError("hostname DNS precisa de ao menos um ponto e ate 253 caracteres")
    rotulos = host.split(".")
    for rotulo in rotulos:
        if not _ROTULO_DNS.fullmatch(rotulo):
            raise ValueError("rotulo invalido (so a-z, 0-9 e hifen, 1 a 63, sem hifen nas pontas)")
        if rotulo.startswith("xn--"):
            raise ValueError("rotulo xn-- (IDN) nao e permitido")
    if rotulos[-1].isdigit():
        raise ValueError("ultimo rotulo numerico (parece IPv4)")
    return host


def _eco(valor: str) -> str:
    """Entrada rejeitada para a mensagem de erro, truncada em ``_MAX_ECO``."""
    return valor if len(valor) <= _MAX_ECO else valor[:_MAX_ECO] + "..."


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


class _SettingsBase(BaseSettings):
    """Fontes, credenciais por arquivo e o que todo processo tem."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        secrets_dir=_SECRETS_DIR_PADRAO,
        extra="ignore",
        # ValidationError nao carrega input_value: erros de nivel de modelo
        # levariam o dict inteiro do env (inclusive SAP_PASS) para o log.
        hide_input_in_errors=True,
    )

    # Credenciais da classe, na ordem em que o validator de fonte as confere.
    _CREDENCIAIS: ClassVar[tuple[str, ...]] = ("database_url",)

    app_env: Literal["dev", "qas", "prd"]
    database_url: SecretStr
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
                    k.lower() for k in init_kwargs if k.lower() in cls._CREDENCIAIS
                ),
            )
        )
        if efetivo is not None:
            return init_settings, file_secret_settings, env_settings, dotenv_settings
        return init_settings, env_settings, dotenv_settings

    # -- Validators de campo ---------------------------------------------------

    @field_validator("*", mode="before")
    @classmethod
    def _strip_credencial(cls, valor: object, info: ValidationInfo) -> object:
        """Remove espacos e o \\n final de arquivo criado com ``echo`` (so credenciais)."""
        if info.field_name not in cls._CREDENCIAIS:
            return valor
        if isinstance(valor, SecretStr):
            return SecretStr(valor.get_secret_value().strip())
        if isinstance(valor, str):
            return valor.strip()
        return valor

    @field_validator("database_url", mode="after")
    @classmethod
    def _database_url_asyncpg(cls, valor: SecretStr) -> SecretStr:
        if not valor.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL deve usar o driver postgresql+asyncpg://")
        return valor

    @field_validator("log_level", mode="before")
    @classmethod
    def _normaliza_log_level(cls, valor: object) -> object:
        return valor.strip().upper() if isinstance(valor, str) else valor

    # -- Validators de modelo --------------------------------------------------

    @model_validator(mode="after")
    def _credenciais_fora_de_dev_devem_vir_de_arquivo(self) -> _SettingsBase:
        """Em qas/prd, toda credencial da classe so vale vinda da fonte de arquivo efetiva.

        Confere: nenhum init kwarg de credencial; o diretorio lido e o
        configurado; o arquivo existe, nao e vazio apos strip e o valor carregado
        e o do arquivo (se o operador esqueceu o secret, o env nao salva o boot).
        """
        if self.app_env == "dev":
            return self
        fontes = _fontes_correntes.get()
        configurado = _dir_configurado(self.model_config)
        for campo in type(self)._CREDENCIAIS:
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


class ApiSettings(_SettingsBase):
    """Processo da API: so o banco. Nunca recebe config nem credencial do SAP."""


class WorkerSettings(_SettingsBase):
    """Processo do worker: banco + SAP + parametros do outbox."""

    # SAP primeiro: mantem as mensagens de erro do SAP como eram na Fase 0.
    _CREDENCIAIS: ClassVar[tuple[str, ...]] = ("sap_user", "sap_pass", "database_url")

    sap_base_url: HttpUrl
    sap_client: str
    sap_prd_hosts: Annotated[tuple[str, ...], NoDecode]
    sap_user: SecretStr
    sap_pass: SecretStr
    sap_timeout_connect_s: Annotated[float, Field(gt=0)] = 5.0
    sap_timeout_read_s: Annotated[float, Field(gt=0)] = 90.0
    sap_decimal_as_string: bool = True  # TODO(decisao #4): confirmar no 1o POST em DEV
    sap_max_tentativas: Annotated[int, Field(ge=1)] = 5
    sap_lock_timeout_s: Annotated[float, Field(gt=0)] = 600.0
    worker_poll_interval_s: Annotated[float, Field(gt=0)] = 2.0

    @property
    def prazo_tentativa_s(self) -> float:
        """Pior caso de uma tentativa: CSRF + POST + refetch do CSRF + POST (D4)."""
        return 4 * (self.sap_timeout_connect_s + self.sap_timeout_read_s)

    # -- Validators de campo ---------------------------------------------------

    @field_validator("sap_prd_hosts", mode="before")
    @classmethod
    def _parse_prd_hosts(cls, valor: object) -> object:
        """CSV -> tuple de hostnames validos; entrada invalida derruba o boot."""
        if isinstance(valor, str):
            itens = [x.strip() for x in valor.split(",")]
        elif isinstance(valor, (list, tuple)):
            itens = [str(x).strip() for x in valor]
        else:
            return valor
        if not any(itens):
            return ()  # tratado como "obrigatorio" no validator after

        hosts: list[str] = []
        for item in itens:
            if not item:
                raise ValueError("SAP_PRD_HOSTS: entrada vazia na lista (virgula sobrando?)")
            if "://" in item:
                raise ValueError(
                    f"SAP_PRD_HOSTS: entrada '{_eco(item)}' contem esquema; use apenas hostname"
                )
            try:
                hosts.append(validar_hostname(item))
            except ValueError as exc:
                motivo = str(exc)
                if ":" in item and not _eh_ip_literal(item):
                    motivo = "contem porta; use apenas hostname"
                raise ValueError(
                    f"SAP_PRD_HOSTS: entrada '{_eco(item)}' invalida: {motivo}"
                ) from None
        return tuple(hosts)

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

    # -- Validators de modelo --------------------------------------------------

    @model_validator(mode="after")
    def _valida_ambiente_vs_host(self) -> WorkerSettings:
        try:
            host = validar_hostname(self.sap_base_url.host or "")
        except ValueError as exc:
            raise ValueError(f"SAP_BASE_URL: host precisa ser hostname DNS ASCII ({exc})") from None
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
    def _lock_maior_que_o_prazo_da_tentativa(self) -> WorkerSettings:
        """D4: o lock nao pode expirar com uma tentativa ainda em andamento."""
        prazo = self.prazo_tentativa_s
        if self.sap_lock_timeout_s <= prazo + 60:
            raise ValueError(
                f"SAP_LOCK_TIMEOUT_S ({self.sap_lock_timeout_s:g} s) deve ser maior que o prazo "
                f"por tentativa ({prazo:g} s) + 60 s"
            )
        return self
