"""Maquina de estados do contrato (ARCHITECTURE §4).

A ``MATRIZ`` espelha a tabela do §4, que e a fonte unica: ``test_states`` le as
21 linhas do ``.md`` e confere campo a campo. Par ``(estado, evento)`` fora da
matriz -> ``InvalidTransitionError``, antes de olhar qualquer dado. E esta
tabela que decide quando NAO reenviar ao SAP: de ``ENVIANDO``, so
``FALHA_ANTES_POST`` e ``LOCK_EXPIRADO_SEM_ENVIO`` voltam para ``NA_FILA``.

``transition`` e pura: nao muta nada, nao le relogio. Devolve a ``Transicao``,
que e o registro do evento para ``contract_events`` (o novo status e ``para``).
``occurred_at`` e ``contract_id`` sao do caso de uso (porta ``Clock``, Fase 2/3).

Dados por transicao, validados de forma ACUMULADA (uma ``DomainValidationError``):

- ``ator.kind`` = coluna Ator da matriz; ``ator.identifier`` nao vazio (strip).
- ``justificativa``: coluna Justif. "sim" -> obrigatoria. "nao" com ator
  user/admin -> opcional. "nao" com worker/system -> proibida (``not_applicable``):
  detalhe tecnico vai em ``detalhe``, nunca em texto livre. Quando vem: 10 a 500
  caracteres depois do strip.
- ``sap_contract_number``: obrigatorio em ``SAP_201`` e ``RECONCILIAR_PARA_CRIADO``
  (ate 10, so digitos ASCII: VBELN, ``TODO(decisao #13)``); proibido nas demais.
- ``detalhe``: so worker/system. Mapping com chave snake_case (ate 40) e valor
  ``int`` ou ``str`` (ate 200); gravado como copia imutavel.

String vazia ou so com espacos conta como ausente.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.errors import ErrorCode, ErrorCollector, InvalidTransitionError

JUSTIFICATIVA_MIN: Final = 10
JUSTIFICATIVA_MAX: Final = 500
SAP_NUMERO_MAX: Final = 10  # VBELN, TODO(decisao #13) formato com a SD
DETALHE_VALOR_MAX: Final = 200

_DIGITOS: Final = re.compile(r"[0-9]+")
_CHAVE_DETALHE: Final = re.compile(r"[a-z][a-z0-9_]{0,39}")
_FORMATO_CHAVE: Final = "chave snake_case com ate 40 caracteres"
_MAQUINA: Final = (ActorKind.WORKER, ActorKind.SYSTEM)

S = ContractStatus
E = TransitionEvent
K = ActorKind


@dataclass(frozen=True, slots=True)
class Ator:
    kind: ActorKind
    identifier: str  # oid Entra ID (user/admin), id do processo (worker), "system"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActorKind):
            raise TypeError("Ator: tipo invalido (kind)")
        if not isinstance(self.identifier, str):
            raise TypeError("Ator: tipo invalido (identifier)")


@dataclass(frozen=True, slots=True)
class Regra:
    para: ContractStatus
    ator: ActorKind
    exige_justificativa: bool = False
    exige_numero_sap: bool = False


@dataclass(frozen=True, slots=True)
class Transicao:
    """Registro do evento (``contract_events``). Sem timestamp: e do caso de uso."""

    de: ContractStatus
    para: ContractStatus
    evento: TransitionEvent
    ator: Ator
    justificativa: str | None
    sap_contract_number: str | None
    detalhe: Mapping[str, str | int]


# fmt: off
MATRIZ: Final[Mapping[tuple[ContractStatus, TransitionEvent], Regra]] = MappingProxyType({
    (S.RASCUNHO, E.SUBMETER): Regra(S.NA_FILA, K.USER),
    (S.RASCUNHO, E.CANCELAR): Regra(S.CANCELADO, K.USER),  # rascunho: sem justificativa
    (S.NA_FILA, E.WORKER_PEGOU): Regra(S.ENVIANDO, K.WORKER),
    (S.NA_FILA, E.CANCELAR): Regra(S.CANCELADO, K.ADMIN, exige_justificativa=True),
    (S.ENVIANDO, E.SAP_201): Regra(S.CRIADO, K.WORKER, exige_numero_sap=True),
    (S.ENVIANDO, E.SAP_4XX_NEGOCIO): Regra(S.ERRO_NEGOCIO, K.WORKER),
    (S.ENVIANDO, E.SAP_4XX_TECNICO): Regra(S.ERRO_TECNICO, K.WORKER),
    # Body NAO enviado: retry permitido.
    (S.ENVIANDO, E.FALHA_ANTES_POST): Regra(S.NA_FILA, K.WORKER),
    (S.ENVIANDO, E.FALHA_ANTES_POST_ESGOTOU): Regra(S.ERRO_TECNICO, K.WORKER),
    # Body enviado (pode ter chegado ao SAP): nunca volta sozinho para a fila.
    (S.ENVIANDO, E.TIMEOUT_APOS_POST): Regra(S.INCERTO, K.WORKER),
    (S.ENVIANDO, E.CONEXAO_CAIDA_APOS_POST): Regra(S.INCERTO, K.WORKER),
    (S.ENVIANDO, E.SAP_5XX_APOS_POST): Regra(S.INCERTO, K.WORKER),
    # Recuperacao de lock expirado: o marcador e a linha em contract_submissions.
    (S.ENVIANDO, E.LOCK_EXPIRADO_SEM_ENVIO): Regra(S.NA_FILA, K.SYSTEM),
    (S.ENVIANDO, E.LOCK_EXPIRADO_COM_ENVIO): Regra(S.INCERTO, K.SYSTEM),
    (S.ERRO_NEGOCIO, E.SUBMETER): Regra(S.NA_FILA, K.USER),
    (S.ERRO_NEGOCIO, E.CANCELAR): Regra(S.CANCELADO, K.USER, exige_justificativa=True),
    (S.ERRO_TECNICO, E.LIBERAR_REENVIO): Regra(S.NA_FILA, K.ADMIN, exige_justificativa=True),
    (S.ERRO_TECNICO, E.CANCELAR): Regra(S.CANCELADO, K.ADMIN, exige_justificativa=True),
    (S.INCERTO, E.RECONCILIAR_PARA_CRIADO): Regra(
        S.CRIADO, K.ADMIN, exige_justificativa=True, exige_numero_sap=True
    ),
    (S.INCERTO, E.LIBERAR_REENVIO): Regra(S.NA_FILA, K.ADMIN, exige_justificativa=True),
    (S.INCERTO, E.CANCELAR): Regra(S.CANCELADO, K.ADMIN, exige_justificativa=True),
})
# fmt: on


def transition(
    atual: ContractStatus,
    evento: TransitionEvent,
    *,
    ator: Ator,
    justificativa: str | None = None,
    sap_contract_number: str | None = None,
    detalhe: Mapping[str, str | int] | None = None,
) -> Transicao:
    # StrEnum tem o hash da string: sem estes checks, "RASCUNHO" acharia a regra.
    if not isinstance(atual, ContractStatus):
        raise TypeError("transition: atual precisa ser ContractStatus")
    if not isinstance(evento, TransitionEvent):
        raise TypeError("transition: evento precisa ser TransitionEvent")
    regra = MATRIZ.get((atual, evento))
    if regra is None:
        raise InvalidTransitionError(atual, evento)
    if not isinstance(ator, Ator):
        raise TypeError("transition: ator precisa ser Ator")

    maquina = regra.ator in _MAQUINA
    col = ErrorCollector()
    if ator.kind is not regra.ator:
        col.adicionar(
            "ator.kind",
            ErrorCode.ACTOR_NOT_ALLOWED,
            esperado=regra.ator.value,
            recebido=ator.kind.value,
        )
    identifier = ator.identifier.strip()
    if not identifier:
        col.adicionar("ator.identifier", ErrorCode.REQUIRED)
    texto = _justificativa(justificativa, regra, maquina, col)
    numero = _numero_sap(sap_contract_number, regra, col)
    extra = _detalhe(detalhe, maquina, col)
    col.levantar_se_houver()

    return Transicao(
        de=atual,
        para=regra.para,
        evento=evento,
        ator=Ator(ator.kind, identifier),
        justificativa=texto,
        sap_contract_number=numero,
        detalhe=extra,
    )


def _texto(bruto: object, path: str, col: ErrorCollector) -> tuple[str | None, bool]:
    """(valor com strip, vazio -> None; tipo ok?)."""
    if bruto is None:
        return None, True
    if isinstance(bruto, str):
        return bruto.strip() or None, True
    col.adicionar(path, ErrorCode.INVALID_TYPE, tipo="texto")
    return None, False


def _justificativa(bruto: object, regra: Regra, maquina: bool, col: ErrorCollector) -> str | None:
    valor, tipo_ok = _texto(bruto, "justificativa", col)
    if valor is None:
        if tipo_ok and regra.exige_justificativa:
            col.adicionar("justificativa", ErrorCode.REQUIRED)
    elif maquina:
        col.adicionar("justificativa", ErrorCode.NOT_APPLICABLE)
    elif len(valor) < JUSTIFICATIVA_MIN:
        col.adicionar("justificativa", ErrorCode.MIN_LENGTH, min=JUSTIFICATIVA_MIN)
    elif len(valor) > JUSTIFICATIVA_MAX:
        col.adicionar("justificativa", ErrorCode.MAX_LENGTH, max=JUSTIFICATIVA_MAX)
    return valor


def _numero_sap(bruto: object, regra: Regra, col: ErrorCollector) -> str | None:
    valor, tipo_ok = _texto(bruto, "sap_contract_number", col)
    if valor is None:
        if tipo_ok and regra.exige_numero_sap:
            col.adicionar("sap_contract_number", ErrorCode.REQUIRED)
    elif not regra.exige_numero_sap:
        col.adicionar("sap_contract_number", ErrorCode.NOT_APPLICABLE)
    elif len(valor) > SAP_NUMERO_MAX:
        col.adicionar("sap_contract_number", ErrorCode.MAX_LENGTH, max=SAP_NUMERO_MAX)
    elif not _DIGITOS.fullmatch(valor):
        col.adicionar("sap_contract_number", ErrorCode.INVALID_FORMAT, formato="somente digitos")
    return valor


def _detalhe(bruto: object, maquina: bool, col: ErrorCollector) -> Mapping[str, str | int]:
    itens = {} if bruto is None else bruto
    if not isinstance(itens, Mapping):
        col.adicionar("detalhe", ErrorCode.INVALID_TYPE, tipo="objeto")
        itens = {}
    elif itens and not maquina:
        col.adicionar("detalhe", ErrorCode.NOT_APPLICABLE)
    for chave, valor in itens.items():
        if not (isinstance(chave, str) and _CHAVE_DETALHE.fullmatch(chave)):
            col.adicionar("detalhe", ErrorCode.INVALID_FORMAT, formato=_FORMATO_CHAVE)
        elif type(valor) is not int and not isinstance(valor, str):  # bool e subclasse de int
            col.adicionar(f"detalhe.{chave}", ErrorCode.INVALID_TYPE, tipo="texto ou inteiro")
        elif isinstance(valor, str) and len(valor) > DETALHE_VALOR_MAX:
            col.adicionar(f"detalhe.{chave}", ErrorCode.MAX_LENGTH, max=DETALHE_VALOR_MAX)
    return MappingProxyType(dict(itens))
