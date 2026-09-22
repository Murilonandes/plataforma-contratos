"""Entrypoint do worker do outbox (``python -m app.entrypoints.worker``).

Stub da Fase 0: loga e sai. O loop real (SKIP LOCKED + envio ao SAP) entra na Fase 2,
dentro de ``_executar``.
"""

from __future__ import annotations

import os
import sys

import structlog

from app.observability.logging import configure_logging


def _executar() -> None:
    structlog.get_logger(__name__).info("worker stub — Fase 2")


def main() -> None:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        _executar()
    except Exception:
        structlog.get_logger(__name__).exception("worker_falhou")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
