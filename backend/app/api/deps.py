"""Dependencias compartilhadas da camada HTTP."""

from __future__ import annotations

from functools import lru_cache

from app.settings import ApiSettings


@lru_cache(maxsize=1)
def get_settings() -> ApiSettings:
    """Settings da API, carregado uma vez (fail-closed no primeiro acesso)."""
    return ApiSettings()
