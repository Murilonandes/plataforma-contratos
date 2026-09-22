"""Entrypoint do worker do outbox (``python -m app.entrypoints.worker``).

Stub da Fase 0: loga e sai. O loop real (SKIP LOCKED + envio ao SAP) entra na Fase 2.
"""

from __future__ import annotations

import sys

import structlog

from app.observability.logging import configure_logging


def main() -> None:
    configure_logging("INFO")
    structlog.get_logger(__name__).info("worker stub — Fase 2")
    sys.exit(0)


if __name__ == "__main__":
    main()
