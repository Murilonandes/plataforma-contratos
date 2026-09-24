"""Entrypoint do worker do outbox (``python -m app.entrypoints.worker``).

Carrega ``WorkerSettings`` (fail-closed: sem banco ou sem config SAP valida, sai
com 1). O loop real (SKIP LOCKED + envio ao SAP) entra na Fase 2, dentro de
``_executar``; por enquanto loga e sai.
"""

from __future__ import annotations

import os
import sys

import structlog
from pydantic import ValidationError

from app.observability.logging import configure_logging
from app.settings import WorkerSettings


def _executar(settings: WorkerSettings) -> None:
    structlog.get_logger(__name__).info("worker stub — Fase 2")


def main() -> None:
    # Bootstrap antes do Settings: o erro fail-closed tambem sai em JSON.
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    log = structlog.get_logger(__name__)
    try:
        settings = WorkerSettings()
    except ValidationError as exc:
        # include_input=False: o input e o env inteiro (inclui SAP_PASS e DATABASE_URL).
        log.critical(
            "startup_falhou_config_invalida",
            erros=exc.errors(include_input=False, include_url=False, include_context=False),
        )
        sys.exit(1)
    configure_logging(settings.log_level)
    try:
        _executar(settings)
    except Exception:
        structlog.get_logger(__name__).exception("worker_falhou")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
