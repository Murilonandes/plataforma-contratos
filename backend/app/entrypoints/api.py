"""Entrypoint da API (``python -m app.entrypoints.api``)."""

from __future__ import annotations

import os
import sys

import structlog
import uvicorn
from pydantic import ValidationError

from app.observability.logging import configure_logging


def main() -> None:
    # Bootstrap antes de qualquer Settings: warnings e o erro fail-closed da
    # factory tambem saem em JSON. create_app reaplica com settings.log_level.
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    log = structlog.get_logger(__name__)
    try:
        uvicorn.run(
            "app.main:create_app",
            factory=True,
            host="0.0.0.0",  # noqa: S104 — roda em container; exposicao controlada pelo Traefik
            port=8000,
            log_config=None,  # mantem o JSON do configure_logging (uvicorn nao sobrescreve)
            proxy_headers=True,
        )
    except ValidationError as exc:
        # include_input=False: o input e o env inteiro (inclui SAP_PASS).
        log.critical(
            "startup_falhou_config_invalida",
            erros=exc.errors(include_input=False, include_url=False, include_context=False),
        )
        sys.exit(1)
    except Exception:
        log.exception("startup_falhou")
        sys.exit(1)


if __name__ == "__main__":
    main()
