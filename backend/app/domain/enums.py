"""Enums do dominio: SO conceitos nossos.

- ``ContractStatus``: os 8 estados da maquina (ARCHITECTURE §4).
- ``TransitionEvent``: os 19 eventos da matriz de 25 transicoes (§4).
- ``ActorKind``: quem dispara a transicao (``user``, ``admin``, ``worker``,
  ``system``), em minusculo como no §4.

Codigos do SAP (PartnerFunction, ConditionType, FormPag, LongTextID, Language,
SalesContractType, StatusBlock...) NAO sao enum: no ``$metadata`` sao
``Edm.String`` com MaxLength. O dominio valida o formato; os valores permitidos
vem de config por sales org e sao validados no caso de uso (ARCHITECTURE §8,
``TODO(decisao #11)``). ``StatusBlock="06"`` e constante do mapper.
"""

from __future__ import annotations

from enum import StrEnum


class ContractStatus(StrEnum):
    RASCUNHO = "RASCUNHO"
    NA_FILA = "NA_FILA"
    ENVIANDO = "ENVIANDO"
    CRIADO = "CRIADO"
    ERRO_NEGOCIO = "ERRO_NEGOCIO"
    ERRO_TECNICO = "ERRO_TECNICO"
    INCERTO = "INCERTO"
    CANCELADO = "CANCELADO"


class TransitionEvent(StrEnum):
    SUBMETER = "SUBMETER"
    CANCELAR = "CANCELAR"
    WORKER_PEGOU = "WORKER_PEGOU"
    SAP_201 = "SAP_201"
    SAP_4XX_NEGOCIO = "SAP_4XX_NEGOCIO"
    SAP_4XX_TECNICO = "SAP_4XX_TECNICO"
    FALHA_ANTES_POST = "FALHA_ANTES_POST"
    FALHA_ANTES_POST_ESGOTOU = "FALHA_ANTES_POST_ESGOTOU"
    TIMEOUT_APOS_POST = "TIMEOUT_APOS_POST"
    CONEXAO_CAIDA_APOS_POST = "CONEXAO_CAIDA_APOS_POST"
    SAP_5XX_APOS_POST = "SAP_5XX_APOS_POST"
    LOCK_EXPIRADO_SEM_ENVIO = "LOCK_EXPIRADO_SEM_ENVIO"
    LOCK_EXPIRADO_COM_ENVIO = "LOCK_EXPIRADO_COM_ENVIO"
    LIBERAR_REENVIO = "LIBERAR_REENVIO"
    RECONCILIAR_PARA_CRIADO = "RECONCILIAR_PARA_CRIADO"
    CONFERENCIA_DIVERGENTE = "CONFERENCIA_DIVERGENTE"
    FALHA_APOS_RESPOSTA = "FALHA_APOS_RESPOSTA"
    FALHA_NAO_CLASSIFICADA_ANTES_ENVIO = "FALHA_NAO_CLASSIFICADA_ANTES_ENVIO"
    FALHA_NAO_CLASSIFICADA_APOS_ENVIO = "FALHA_NAO_CLASSIFICADA_APOS_ENVIO"


class ActorKind(StrEnum):
    USER = "user"
    ADMIN = "admin"
    WORKER = "worker"
    SYSTEM = "system"
