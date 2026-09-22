"""Dependencias compartilhadas da camada HTTP."""

from __future__ import annotations

from functools import lru_cache

from app.settings import Settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings do processo, carregado uma vez (fail-closed no primeiro acesso)."""
    return Settings()
