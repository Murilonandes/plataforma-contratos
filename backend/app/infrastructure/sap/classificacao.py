"""Classificacao do resultado do SAP (Tarefa 2.7, D8). Puro, no gate de mutacao.

Regra da §4: **"o POST pode ter saido?"** Nao -> retry (``FALHA_ANTES_POST``);
sim ou na duvida -> ``INCERTO``. Nada aqui faz I/O; o gateway chama.

- **CSRF:** qualquer excecao (inclusive nao mapeada e prazo estourado) e qualquer
  status diferente de 200-com-token -> ``FALHA_ANTES_POST``; ``401``/``403`` ->
  ``SAP_4XX_TECNICO`` sem retry (repetir login pode bloquear o usuario tecnico).
- **Excecao no POST:** ``Connect*``/``PoolTimeout`` e falta de token dentro do
  fluxo do 403 CSRF (nenhum POST processado saiu) -> ``FALHA_ANTES_POST``;
  ``Write*``/``ReadError``/``RemoteProtocolError`` -> ``CONEXAO_CAIDA_APOS_POST``;
  ``ReadTimeout`` -> ``TIMEOUT_APOS_POST``; o resto e dividido pelo marcador
  ``request_sent_at`` (``FALHA_NAO_CLASSIFICADA_ANTES/APOS_ENVIO``).
- **Resposta do POST:** precedencia da §4 (5xx; tecnico ``{401,403,404,405,415}``;
  negocio ``{400,409,422}``; outro 4xx com ``error.details`` negocio, sem, tecnico);
  ``201`` so vira ``SAP_201`` com ``SalesContract`` valido (validado e canonizado
  pela propria ``transition``, D9); o resto (1xx, 2xx != 201, 3xx, fora de
  100-599, 201 fora do formato) -> ``FALHA_APOS_RESPOSTA`` -> ``INCERTO`` (D12).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import httpx

from app.application.ports import MensagemSap, RespostaSap
from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.states import Ator, transition
from app.infrastructure.sap.client import CsrfIndisponivel
from app.infrastructure.sap.respostas import ler_erro, ler_sucesso

E = TransitionEvent

TECNICO_4XX: Final = frozenset({401, 403, 404, 405, 415})
NEGOCIO_4XX: Final = frozenset({400, 409, 422})
_CSRF_SEM_RETRY: Final = frozenset({401, 403})

_ANTES_DOS_BYTES: Final = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_CONEXAO_CAIDA: Final = (
    httpx.WriteError,
    httpx.WriteTimeout,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)
# so para validar e canonizar o SalesContract; sem literal (mutante equivalente)
_VALIDADOR: Final = Ator(ActorKind.WORKER, ActorKind.WORKER.value)


@dataclass(frozen=True, slots=True)
class Classificacao:
    evento: TransitionEvent
    detalhe: Mapping[str, str | int]
    sap_contract_number: str | None = None
    mensagens: tuple[MensagemSap, ...] = ()


def classificar_csrf(
    resultado: RespostaSap | Exception, *, token_obtido: bool
) -> Classificacao | None:
    """``None`` = token ok."""
    if isinstance(resultado, Exception):
        return Classificacao(
            E.FALHA_ANTES_POST, {"fase": "csrf", "error_class": type(resultado).__name__}
        )
    detalhe: dict[str, str | int] = {"fase": "csrf", "status": resultado.status}
    if resultado.status in _CSRF_SEM_RETRY:
        return Classificacao(E.SAP_4XX_TECNICO, detalhe)
    if resultado.status == 200 and token_obtido:
        return None
    return Classificacao(E.FALHA_ANTES_POST, detalhe)


def classificar_excecao_post(exc: Exception, *, marcador_commitado: bool) -> Classificacao:
    if isinstance(exc, (CsrfIndisponivel, *_ANTES_DOS_BYTES)):
        evento = E.FALHA_ANTES_POST
    elif isinstance(exc, _CONEXAO_CAIDA):
        evento = E.CONEXAO_CAIDA_APOS_POST
    elif isinstance(exc, httpx.ReadTimeout):
        evento = E.TIMEOUT_APOS_POST
    elif marcador_commitado:
        evento = E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO
    else:
        evento = E.FALHA_NAO_CLASSIFICADA_ANTES_ENVIO
    return Classificacao(evento, {"fase": "post", "error_class": type(exc).__name__})


def _sucesso(resposta: RespostaSap) -> Classificacao:
    try:
        lida = ler_sucesso(resposta)
        numero = transition(
            ContractStatus.ENVIANDO,
            E.SAP_201,
            ator=_VALIDADOR,
            sap_contract_number=lida.sales_contract,
        ).sap_contract_number
    except Exception as exc:  # D12: qualquer falha nossa depois do 201 -> INCERTO
        return Classificacao(
            E.FALHA_APOS_RESPOSTA,
            {"fase": "post", "status": 201, "error_class": type(exc).__name__},
        )
    return Classificacao(
        E.SAP_201,
        {"fase": "post", "status": 201},
        sap_contract_number=numero,
        mensagens=lida.avisos,
    )


def classificar_resposta_post(resposta: RespostaSap) -> Classificacao:
    status = resposta.status
    if status == 201:
        return _sucesso(resposta)
    detalhe: dict[str, str | int] = {"fase": "post", "status": status}
    if not 400 <= status <= 599:
        return Classificacao(E.FALHA_APOS_RESPOSTA, detalhe)
    erro = ler_erro(resposta.corpo)
    mensagens = erro.mensagens if erro is not None else ()
    if status >= 500:
        evento = E.SAP_5XX_APOS_POST
    elif status in TECNICO_4XX:
        evento = E.SAP_4XX_TECNICO
    elif status in NEGOCIO_4XX or (erro is not None and erro.tem_details):
        evento = E.SAP_4XX_NEGOCIO
    else:
        evento = E.SAP_4XX_TECNICO
    return Classificacao(evento, detalhe, mensagens=mensagens)
