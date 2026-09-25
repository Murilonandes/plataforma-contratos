"""Client HTTP do ``CriaContrato`` (httpx), CRU: nao classifica nada (Tarefa 2.5).

- **Sessao:** um ``httpx.AsyncClient`` com Basic Auth do usuario tecnico,
  parametros ``sap-client`` e ``saml2=disabled``, timeouts connect/read da
  config e ``follow_redirects=False`` EXPLICITO (3xx volta como resposta; a
  §4 manda para ``INCERTO``). Cookies da sessao seguem do GET do token para o POST.
- **CSRF:** ``GET {base}`` com ``x-csrf-token: Fetch``; token em cache so com 200
  e header presente.
- **POST:** ``POST {base}CriaContrato`` com ``content=`` os bytes exatos do
  ``to_json`` (nunca ``json=``); ``Content-Type`` com ``IEEE754Compatible=true``
  quando ``decimal_as_string``.
- **403 com ``x-csrf-token: Required``:** invalida o token, refaz o fetch e
  reenvia UMA vez. Se faltar token dentro do ``post_criar_contrato`` (cache vazio
  ou refetch falhou), levanta ``CsrfIndisponivel``/``FalhaNoRefetchCsrf``: nenhum
  POST processado saiu (o unico que chegou foi recusado sem processar).
- **Excecoes de transporte** do httpx sobem sem reinterpretar (classificacao e
  da 2.7, pela regra "o POST pode ter saido?").
- **Log allowlist (CLAUDE.md):** uma linha ``sap_http`` por chamada, so com
  ``method``, URL SEM query, ``status`` (nulo se houve excecao), ``duration_ms``,
  ``correlation_id`` e ``contract_id``. Nunca headers nem corpo, nem em DEBUG:
  os loggers ``httpx``/``httpcore`` ficam em WARNING (em DEBUG eles logariam a
  URL com query e detalhes da conexao).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self
from uuid import UUID

import httpx
import structlog

from app.application.ports import RespostaSap

_log = structlog.get_logger("app.sap")

_CT_JSON = "application/json"
_CT_IEEE754 = "application/json;IEEE754Compatible=true"


@dataclass(frozen=True, slots=True)
class ConfigSap:
    base_url: str
    sap_client: str
    usuario: str
    senha: str = field(repr=False)
    timeout_connect_s: float
    timeout_read_s: float
    decimal_as_string: bool


class CsrfIndisponivel(Exception):
    """Faltou token CSRF dentro do ``post_criar_contrato``: nenhum POST processado saiu."""

    def __init__(self, resposta: RespostaSap | None, mensagem: str = "") -> None:
        self.resposta = resposta
        super().__init__(mensagem or "token CSRF indisponivel antes do POST")


class FalhaNoRefetchCsrf(CsrfIndisponivel):
    """O refetch do token depois de um 403 CSRF falhou (excecao ou resposta sem token)."""

    def __init__(self, resposta: RespostaSap | None) -> None:
        super().__init__(resposta, "refetch do token CSRF falhou depois de 403 CSRF")


def _resposta(r: httpx.Response) -> RespostaSap:
    return RespostaSap(
        status=r.status_code,
        headers={k.lower(): v for k, v in r.headers.items()},
        corpo=r.content,
    )


def _csrf_required(r: httpx.Response) -> bool:
    return r.status_code == 403 and r.headers.get("x-csrf-token", "").lower() == "required"


class ClienteSap:
    def __init__(
        self, config: ConfigSap, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        for nome in ("httpx", "httpcore"):
            logging.getLogger(nome).setLevel(logging.WARNING)
        self._config = config
        base = config.base_url if config.base_url.endswith("/") else config.base_url + "/"
        self._url_token = base
        self._url_post = base + "CriaContrato"
        self._params = {"sap-client": config.sap_client, "saml2": "disabled"}
        self._http = httpx.AsyncClient(
            auth=httpx.BasicAuth(config.usuario, config.senha),
            timeout=httpx.Timeout(config.timeout_read_s, connect=config.timeout_connect_s),
            follow_redirects=False,
            transport=transport,
        )
        self._token: str | None = None

    # -- estado observavel (testes e readiness) --------------------------------------------

    @property
    def timeout(self) -> httpx.Timeout:
        return self._http.timeout

    @property
    def segue_redirect(self) -> bool:
        return self._http.follow_redirects

    @property
    def token_em_cache(self) -> str | None:
        return self._token

    # -- ciclo de vida ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- chamadas --------------------------------------------------------------------------

    async def _chamar(
        self,
        metodo: str,
        url: str,
        *,
        headers: dict[str, str],
        correlation_id: str,
        contract_id: UUID,
        content: bytes | None = None,
    ) -> httpx.Response:
        inicio = time.perf_counter()
        status: int | None = None
        try:
            resp = await self._http.request(
                metodo, url, params=self._params, headers=headers, content=content
            )
            status = resp.status_code
            return resp
        finally:
            _log.info(
                "sap_http",
                method=metodo,
                url=str(httpx.URL(url).copy_with(query=None)),
                status=status,
                duration_ms=int((time.perf_counter() - inicio) * 1000),
                correlation_id=correlation_id,
                contract_id=str(contract_id),
            )

    async def buscar_token(self, *, correlation_id: str, contract_id: UUID) -> RespostaSap:
        """GET do token CSRF. Cacheia so com 200 e header presente."""
        self._token = None
        resp = await self._chamar(
            "GET",
            self._url_token,
            headers={"x-csrf-token": "Fetch", "accept": _CT_JSON},
            correlation_id=correlation_id,
            contract_id=contract_id,
        )
        token = resp.headers.get("x-csrf-token", "")
        if resp.status_code == 200 and token and token.lower() != "required":
            self._token = token
        return _resposta(resp)

    async def _post(
        self, corpo: bytes, token: str, *, correlation_id: str, contract_id: UUID
    ) -> httpx.Response:
        try:
            return await self._chamar_post(
                corpo, token, correlation_id=correlation_id, contract_id=contract_id
            )
        except httpx.TransportError:
            self._token = None  # SAP inalcancavel: a proxima tentativa busca token de novo
            raise

    async def _chamar_post(
        self, corpo: bytes, token: str, *, correlation_id: str, contract_id: UUID
    ) -> httpx.Response:
        return await self._chamar(
            "POST",
            self._url_post,
            headers={
                "x-csrf-token": token,
                "content-type": _CT_IEEE754 if self._config.decimal_as_string else _CT_JSON,
                "accept": _CT_JSON,
            },
            content=corpo,
            correlation_id=correlation_id,
            contract_id=contract_id,
        )

    async def _token_para_post(
        self, *, correlation_id: str, contract_id: UUID, refetch: bool
    ) -> str:
        erro_cls = FalhaNoRefetchCsrf if refetch else CsrfIndisponivel
        try:
            resposta = await self.buscar_token(
                correlation_id=correlation_id, contract_id=contract_id
            )
        except Exception as exc:
            raise (FalhaNoRefetchCsrf(None) if refetch else CsrfIndisponivel(None)) from exc
        if self._token is None:
            raise erro_cls(resposta)
        return self._token

    async def post_criar_contrato(
        self, corpo: bytes, *, correlation_id: str, contract_id: UUID
    ) -> RespostaSap:
        """POST com os bytes exatos; 403 CSRF: refetch + 1 reenvio. Nao classifica."""
        token = self._token or await self._token_para_post(
            correlation_id=correlation_id, contract_id=contract_id, refetch=False
        )
        resp = await self._post(
            corpo, token, correlation_id=correlation_id, contract_id=contract_id
        )
        if _csrf_required(resp):
            self._token = None
            token = await self._token_para_post(
                correlation_id=correlation_id, contract_id=contract_id, refetch=True
            )
            resp = await self._post(
                corpo, token, correlation_id=correlation_id, contract_id=contract_id
            )
        return _resposta(resp)
