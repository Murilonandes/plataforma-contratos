"""Logging estruturado (structlog -> JSON no stdout).

Toda saida passa pelo stdlib ``logging`` para que logs de bibliotecas
(uvicorn, sqlalchemy, httpx) e ``warnings.warn`` saiam no mesmo formato JSON.
Chaves sensiveis sao redigidas antes da serializacao. Os entrypoints chamam
``configure_logging`` antes de instanciar o Settings.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

REDACTED = "***REDACTED***"

_CHAVES_SENSIVEIS = frozenset(
    {"authorization", "password", "passwd", "secret", "token", "sap_pass", "cookie"}
)


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Substitui o valor de chaves sensiveis (case-insensitive) por ``REDACTED``."""
    for chave in event_dict:
        if chave.lower() in _CHAVES_SENSIVEIS:
            event_dict[chave] = REDACTED
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configura structlog + stdlib para emitir JSON no stdout."""
    nivel = logging.getLevelNamesMapping()[level.upper()]

    pre_chain: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
    ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *pre_chain,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(nivel)

    # warnings.warn (bibliotecas, pydantic-settings...) vira log do "py.warnings",
    # em JSON, em vez de texto cru no stderr.
    logging.captureWarnings(True)
