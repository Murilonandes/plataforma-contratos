"""Ambiente do Alembic (async, asyncpg) para o schema ``contratos``.

- URL: ``config.attributes["url"]`` (testes) ou ``ApiSettings().database_url``
  (env em dev, Docker secret em qas/prd). Nunca logada.
- ``CREATE SCHEMA IF NOT EXISTS contratos`` antes de tudo: o ``alembic_version``
  mora nele (``version_table_schema``).
- So o schema ``contratos`` e comparado no ``alembic check`` (``include_name``).
- Logs em JSON (``configure_logging``), como todo entrypoint.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.db.modelos import SCHEMA, metadata
from app.observability.logging import configure_logging

config = context.config
configure_logging(os.environ.get("LOG_LEVEL", "INFO"))


def _url() -> str:
    url = config.attributes.get("url")
    if isinstance(url, str):
        return url
    from app.settings import ApiSettings  # so aqui: testes passam a URL direto

    return ApiSettings().database_url.get_secret_value()


def _incluir(nome: str | None, tipo: str, _pai: object) -> bool:
    return tipo != "schema" or nome == SCHEMA


def _migrar(conexao: Connection) -> None:
    conexao.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    context.configure(
        connection=conexao,
        target_metadata=metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_name=_incluir,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _online() -> None:
    engine = create_async_engine(_url())
    try:
        async with engine.connect() as conexao:
            await conexao.run_sync(_migrar)
            await conexao.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("modo offline nao suportado: rode contra o banco")
asyncio.run(_online())
