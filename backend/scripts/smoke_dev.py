"""Smoke real em DEV (Tarefa 2.12): cria UM contrato de verdade no SAP DEV.

So com autorizacao explicita do dono do projeto, a cada execucao. Uso (em
``backend/``, com o banco local do compose e as credenciais do usuario tecnico
em Docker secret/arquivo ou env de dev):

    uv run python scripts/smoke_dev.py

Caminho completo da plataforma: payload de referencia (``docs/sap``) ->
``submeter_contrato`` (snapshot + job no outbox) -> ``ProcessadorOutbox``
(conferencia, CSRF, marcador, POST, classificacao, transicao).

Guardas:
- ``WorkerSettings`` com o guard DEV x PRD ativo, e o script recusa ``APP_ENV``
  diferente de ``dev``;
- ``PedidoSysFertil`` e ``PurchaseOrderByCustomer`` unicos por execucao, com
  prefixo ``SMOKE-<timestamp>``;
- confirmacao DIGITADA antes de gravar qualquer coisa (recusou: nada e gravado,
  nada e enviado);
- recusa rodar se o outbox tiver QUALQUER job pendente (o processador pega o
  proximo da fila: so assim o job enviado e o deste smoke) e confere depois que o
  job processado foi o dele. Pare o worker do compose antes de rodar.

Saida: evento, status e numero SAP no terminal, e a resposta crua (status,
header ``sap-messages``, corpo) em ``backend/.smoke/`` (fora do git) para virar
fixture ANONIMIZADA em ``tests/contract/fixtures/`` depois.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.application.ports import EntradaParcelas, NovoContrato  # noqa: E402
from app.application.process_outbox_job import ConfigWorker, ProcessadorOutbox  # noqa: E402
from app.application.snapshot import montar_contrato  # noqa: E402
from app.application.submit_contract import PedidoSubmissao, submeter_contrato  # noqa: E402
from app.domain.contract import ESPECIFICACOES, Tipo  # noqa: E402
from app.domain.enums import ActorKind  # noqa: E402
from app.domain.states import Ator  # noqa: E402
from app.entrypoints.worker import config_sap  # noqa: E402
from app.infrastructure.db.modelos import contract_submissions, outbox_jobs  # noqa: E402
from app.infrastructure.db.uow import fabrica_de_uow  # noqa: E402
from app.infrastructure.relogio import RelogioDoSistema  # noqa: E402
from app.infrastructure.sap.client import ClienteSap  # noqa: E402
from app.infrastructure.sap.gateway import GatewaySap  # noqa: E402
from app.infrastructure.sap.mapper import to_json, to_payload  # noqa: E402
from app.observability.logging import configure_logging  # noqa: E402
from app.observability.metricas import MetricasEmLog  # noqa: E402
from app.settings import WorkerSettings  # noqa: E402

PAYLOAD = BACKEND.parent / "docs" / "sap" / "payload_exemplo.json"
SAIDA = BACKEND / ".smoke"
CONFIRMACAO = "ENVIAR"

_DECIMAIS = {c.odata for specs in ESPECIFICACOES.values() for c in specs if c.tipo is Tipo.DECIMAL}
_DATAS = {c.odata for specs in ESPECIFICACOES.values() for c in specs if c.tipo is Tipo.DATA}


def _converter(no: Any, chave: str | None = None) -> Any:
    if isinstance(no, dict):
        return {k: _converter(v, k) for k, v in no.items()}
    if isinstance(no, list):
        return [_converter(v, chave) for v in no]
    if chave in _DECIMAIS and isinstance(no, int | Decimal) and not isinstance(no, bool):
        return Decimal(no)
    if chave in _DATAS and isinstance(no, str):
        return date.fromisoformat(no)
    return no


def marca_da_execucao(agora: datetime) -> str:
    """``SMOKE-AAAAMMDDHHMMSS`` (UTC): cabe em ``PurchaseOrderByCustomer`` (35)."""
    return "SMOKE-" + agora.astimezone(UTC).strftime("%Y%m%d%H%M%S")


def montar_entrada(marca: str) -> tuple[dict[str, Any], EntradaParcelas]:
    """Payload de referencia sem ``StatusBlock`` e sem ``to_FormPag`` (as parcelas do
    exemplo viram a ENTRADA do calculo: total, pesos iguais, datas e FormPag)."""
    bruto = json.loads(PAYLOAD.read_text(encoding="utf-8"), parse_float=Decimal)
    bruto.pop("StatusBlock")
    parcelas = bruto.pop("to_FormPag")
    entrada: dict[str, Any] = _converter(bruto)
    entrada["PedidoSysFertil"] = marca
    entrada["PurchaseOrderByCustomer"] = marca
    return entrada, EntradaParcelas(
        total=sum((Decimal(str(p["Valor"])) for p in parcelas), Decimal("0.00")),
        pesos=tuple(1 for _ in parcelas),
        datas=tuple(date.fromisoformat(p["Data"]) for p in parcelas),
        form_pag=parcelas[0]["FormPag"],
    )


def confirmar(resumo: str, perguntar: Callable[[str], str]) -> bool:
    print(resumo)
    return perguntar(f"Digite {CONFIRMACAO} para criar o contrato no SAP DEV: ").strip() == (
        CONFIRMACAO
    )


async def executar(settings: WorkerSettings, perguntar: Callable[[str], str]) -> int:
    relogio = RelogioDoSistema()
    marca = marca_da_execucao(relogio.agora())
    entrada, parcelas = montar_entrada(marca)
    contrato = montar_contrato(entrada, parcelas)  # valida tudo ANTES de perguntar
    corpo = to_json(to_payload(contrato, decimal_as_string=settings.sap_decimal_as_string))
    resumo = (
        f"SAP: {settings.sap_base_url.host} (sap-client={settings.sap_client})\n"
        f"Pedido: {marca} | total {parcelas.total} em {len(parcelas.pesos)} parcelas | "
        f"{len(contrato.items)} item(ns) | {len(corpo)} bytes"
    )
    if not confirmar(resumo, perguntar):
        print("Cancelado: nada foi gravado nem enviado.")
        return 2

    engine = create_async_engine(settings.database_url.get_secret_value())
    nova_uow = fabrica_de_uow(engine, relogio)
    try:
        pendentes = await jobs_pendentes(engine)
        if pendentes:
            print(f"Recusado: {pendentes} job(s) pendente(s) no outbox; nada foi gravado.")
            return 3
        cid = uuid4()
        async with nova_uow() as uow:
            await uow.contratos.inserir(
                NovoContrato(
                    id=cid,
                    origin="API",
                    created_by="smoke_dev",
                    idempotency_key=marca,
                    pedido_sysfertil=marca,
                    entrada={},
                )
            )
            await uow.commit()
        submetido = await submeter_contrato(
            PedidoSubmissao(
                contract_id=cid,
                entrada=entrada,
                parcelas=parcelas,
                ator=Ator(ActorKind.USER, "smoke_dev"),
                correlation_id=marca,
            ),
            nova_uow=nova_uow,
            relogio=relogio,
        )
        async with ClienteSap(config_sap(settings)) as cliente:
            resultado = await ProcessadorOutbox(
                nova_uow=nova_uow,
                relogio=relogio,
                gateway=GatewaySap(cliente),
                metricas=MetricasEmLog(),
                config=ConfigWorker(
                    worker_id="smoke_dev",
                    lock_timeout=timedelta(seconds=settings.sap_lock_timeout_s),
                    max_tentativas=1,
                ),
                corpo_de=lambda c: to_json(
                    to_payload(c, decimal_as_string=settings.sap_decimal_as_string)
                ),
            ).processar_proximo()
        if resultado is None or resultado.job.id != submetido.job_id:
            print("ATENCAO: o job processado nao foi o deste smoke (worker do compose ligado?).")
            print(f"contract_id do smoke: {cid} - confira o estado antes de qualquer reenvio.")
            return 4
        async with nova_uow() as uow:
            registro = await uow.contratos.obter(cid)
        await _guardar_resposta(engine, cid, marca)
    finally:
        await engine.dispose()

    print(f"contract_id: {cid}")
    print(f"evento: {resultado.evento if resultado else None}")
    print(f"status: {registro.status if registro else None}")
    print(f"numero SAP: {registro.sap_contract_number if registro else None}")
    return 0


async def jobs_pendentes(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        n = await conn.scalar(
            select(func.count())
            .select_from(outbox_jobs)
            .where(outbox_jobs.c.concluido_em.is_(None))
        )
    return int(n or 0)


async def _guardar_resposta(engine: AsyncEngine, cid: UUID, marca: str) -> None:
    """Resposta crua para virar fixture anonimizada (nunca request/headers de auth)."""
    async with engine.connect() as conn:
        linha = (
            await conn.execute(
                select(
                    contract_submissions.c.response_status,
                    contract_submissions.c.response_body,
                    contract_submissions.c.sap_messages,
                ).where(contract_submissions.c.contract_id == cid)
            )
        ).first()
    if linha is None:
        return
    SAIDA.mkdir(exist_ok=True)
    corpo = linha.response_body
    destino = SAIDA / f"{marca}.json"
    destino.write_text(
        json.dumps(
            {
                "response_status": linha.response_status,
                "response_body": None if corpo is None else bytes(corpo).decode("utf-8", "replace"),
                "sap_messages": linha.sap_messages,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"resposta crua em {destino} (anonimizar antes de virar fixture)")


def main() -> int:
    configure_logging("INFO")
    settings = WorkerSettings()  # guard DEV x PRD do settings
    if settings.app_env != "dev":
        print(f"Recusado: o smoke so roda com APP_ENV=dev (atual: {settings.app_env}).")
        return 1
    return asyncio.run(executar(settings, input))


if __name__ == "__main__":
    sys.exit(main())
