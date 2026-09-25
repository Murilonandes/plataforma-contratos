"""Consultas da API ao banco para saude (Tarefa 2.10). A API nunca chama o SAP.

- ``banco_ok``: ``SELECT 1`` (readiness ``db``).
- ``ultimo_csrf_ok``: instante do ultimo fetch de CSRF bem-sucedido do worker,
  gravado junto com o marcador de envio (``/health/sap``, informativo).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.infrastructure.db.modelos import sap_heartbeat


async def banco_ok(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        return bool(await conn.scalar(text("SELECT 1")) == 1)


async def ultimo_csrf_ok(engine: AsyncEngine) -> datetime | None:
    async with engine.connect() as conn:
        valor: datetime | None = await conn.scalar(
            select(sap_heartbeat.c.ultimo_csrf_ok).where(sap_heartbeat.c.id == 1)
        )
    return valor
