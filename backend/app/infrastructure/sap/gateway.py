"""Adapter da porta ``SapContractGateway`` (Tarefa 2.7): ``ClienteSap`` + classificacao.

Nunca levanta excecao (fora ``CancelledError``/``BaseException``: desligamento do
worker; ai quem decide e o recover pelo marcador). Cada chamada tem um **prazo
total** com ``asyncio.timeout`` (D4): ``preparar`` = connect + read;
``criar_contrato`` = 3 x (connect + read) (POST + refetch do CSRF + POST).
Prazo estourado e excecao nao listada na §4: no CSRF -> ``FALHA_ANTES_POST``;
no POST (sempre depois do marcador) -> ``FALHA_NAO_CLASSIFICADA_APOS_ENVIO``
-> ``INCERTO``. Falha no nosso processamento depois de ler a resposta ->
``FALHA_APOS_RESPOSTA`` -> ``INCERTO`` (D12), com a resposta crua junto.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

from app.application.ports import DesfechoCsrf, DesfechoPost, RespostaSap
from app.domain.enums import TransitionEvent
from app.infrastructure.sap.classificacao import (
    Classificacao,
    classificar_csrf,
    classificar_excecao_post,
    classificar_resposta_post,
)
from app.infrastructure.sap.client import ClienteSap

_CHAMADAS_NO_POST = 3  # POST + refetch do token + POST (403 CSRF, §4)


class GatewaySap:
    def __init__(self, cliente: ClienteSap) -> None:
        self._cliente = cliente
        unidade = (cliente.timeout.connect or 0.0) + (cliente.timeout.read or 0.0)
        self.prazo_csrf_s = unidade
        self.prazo_post_s = _CHAMADAS_NO_POST * unidade

    async def preparar(self, *, correlation_id: str, contract_id: UUID) -> DesfechoCsrf:
        if self._cliente.token_em_cache is not None:
            return DesfechoCsrf.ok()
        c: Classificacao | None
        try:
            async with asyncio.timeout(self.prazo_csrf_s):
                resposta = await self._cliente.buscar_token(
                    correlation_id=correlation_id, contract_id=contract_id
                )
        except Exception as exc:  # §4: qualquer falha no CSRF e antes do POST
            c = classificar_csrf(exc, token_obtido=False)
        else:
            c = classificar_csrf(resposta, token_obtido=self._cliente.token_em_cache is not None)
        return DesfechoCsrf.ok() if c is None else DesfechoCsrf(c.evento, c.detalhe)

    async def criar_contrato(
        self, corpo: bytes, *, correlation_id: str, contract_id: UUID
    ) -> DesfechoPost:
        inicio = time.perf_counter()
        resposta: RespostaSap | None = None
        try:
            async with asyncio.timeout(self.prazo_post_s):
                lida = await self._cliente.post_criar_contrato(
                    corpo, correlation_id=correlation_id, contract_id=contract_id
                )
        except Exception as exc:  # classificada pela §4, nunca sobe
            c = classificar_excecao_post(exc, marcador_commitado=True)
        else:
            resposta = lida
            try:
                c = classificar_resposta_post(lida)
            except Exception as exc:  # D12: falha nossa depois de ler o status
                c = Classificacao(
                    TransitionEvent.FALHA_APOS_RESPOSTA,
                    {"fase": "post", "status": lida.status, "error_class": type(exc).__name__},
                )
        return DesfechoPost(
            evento=c.evento,
            sap_contract_number=c.sap_contract_number,
            resposta=resposta,
            duracao_ms=int((time.perf_counter() - inicio) * 1000),
            mensagens=c.mensagens,
            detalhe=c.detalhe,
        )
