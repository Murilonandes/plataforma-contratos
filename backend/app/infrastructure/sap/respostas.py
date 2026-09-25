"""Parser das respostas do ``CriaContrato`` (Tarefa 2.6). Funcoes puras.

- **Erro** (formato OData V4 do RAP): ``error.code``, ``error.message`` e
  ``error.details[]`` (``code``, ``message``, ``target``) viram ``MensagemSap``.
  ``ErroSap.tem_details`` alimenta a linha 5 da precedencia da §4 (4xx com
  ``error.details`` -> negocio). Corpo que nao e erro OData -> ``None``.
- **Target -> path do dominio:** so campos do CABECALHO, sem navegacao. Navegacao
  vem com a chave do SAP (``to_Item(...SalesContractItem='000010')/Material``),
  que nao da o indice do nosso payload sem inventar regra de numeracao:
  ``target`` fica cru e ``path`` ``None`` (``TODO(decisao #3)``, rever com as
  respostas reais do smoke).
- **Sucesso (201):** ``SalesContract`` CRU (quem normaliza e recusa letras, mais
  de 10 digitos e so zeros e a ``transition``, D9) + avisos do header
  ``sap-messages``. 201 fora do formato levanta ``RespostaInvalida`` (o gateway
  classifica como ``FALHA_APOS_RESPOSTA`` -> ``INCERTO``, D12).
- ``sap-messages`` e so informativo: fora do formato vira ``()``, nunca derruba
  um 201.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.application.ports import MensagemSap, RespostaSap
from app.domain.contract import CABECALHO

_CAMPOS_CABECALHO: Final = frozenset(c.odata for c in CABECALHO)


class RespostaInvalida(Exception):
    """Resposta lida, mas fora do formato esperado (D12: nunca retry, nunca ERRO_TECNICO)."""


@dataclass(frozen=True, slots=True)
class ErroSap:
    code: str
    message: str
    tem_details: bool
    mensagens: tuple[MensagemSap, ...]  # a do topo primeiro, depois os details


@dataclass(frozen=True, slots=True)
class Sucesso:
    sales_contract: str  # cru, como veio
    avisos: tuple[MensagemSap, ...]


def _rejeitar_constante(nome: str) -> Any:
    raise ValueError(nome)


def _json(corpo: bytes | str) -> Any:
    """``json.loads`` estrito (sem NaN/Infinity). Falha -> ``ValueError``."""
    try:
        return json.loads(corpo, parse_constant=_rejeitar_constante)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("json invalido") from exc


def mapear_target(target: str | None) -> str | None:
    if not target:
        return None
    campo = target.removeprefix("/")
    return campo if campo in _CAMPOS_CABECALHO else None


def _texto(valor: object) -> str:
    return "" if valor is None else str(valor)


def _mensagem(d: Mapping[str, Any]) -> MensagemSap:
    target = _texto(d.get("target")) or None
    return MensagemSap(
        code=_texto(d.get("code")),
        message=_texto(d.get("message")),
        target=target,
        path=mapear_target(target),
    )


def _mensagens(lista: object) -> tuple[MensagemSap, ...]:
    if not isinstance(lista, list):
        return ()
    return tuple(
        _mensagem(d)
        for d in lista
        if isinstance(d, dict) and (d.get("code") is not None or d.get("message") is not None)
    )


def ler_erro(corpo: bytes) -> ErroSap | None:
    try:
        dados = _json(corpo)
    except ValueError:
        return None
    if not isinstance(dados, dict) or not isinstance(dados.get("error"), dict):
        return None
    erro: dict[str, Any] = dados["error"]
    detalhes = _mensagens(erro.get("details"))
    topo = _mensagem(erro)
    return ErroSap(
        code=topo.code,
        message=topo.message,
        tem_details=bool(detalhes),
        mensagens=(topo, *detalhes),
    )


def ler_avisos(headers: Mapping[str, str]) -> tuple[MensagemSap, ...]:
    bruto = next((v for k, v in headers.items() if k.lower() == "sap-messages"), None)
    if not bruto:
        return ()
    try:
        dados = _json(bruto)
    except ValueError:
        return ()
    return _mensagens([dados] if isinstance(dados, dict) else dados)


def ler_sucesso(resposta: RespostaSap) -> Sucesso:
    if resposta.status != 201:
        raise RespostaInvalida(f"ler_sucesso chamado com status {resposta.status}")
    try:
        dados = _json(resposta.corpo)
    except ValueError:
        raise RespostaInvalida("201 sem JSON valido") from None
    if not isinstance(dados, dict):
        raise RespostaInvalida("201 com JSON que nao e objeto")
    numero = dados.get("SalesContract")
    if numero is None:
        raise RespostaInvalida("201 sem SalesContract")
    if not isinstance(numero, str):
        raise RespostaInvalida("201 com SalesContract que nao e texto")
    return Sucesso(sales_contract=numero, avisos=ler_avisos(resposta.headers))
