"""Adapter da porta ``Clock`` (D5): o unico lugar que le o relogio do sistema."""

from __future__ import annotations

from datetime import UTC, datetime


class RelogioDoSistema:
    def agora(self) -> datetime:
        return datetime.now(UTC)
