"""Logging estruturado (structlog -> JSON no stdout).

Toda saida passa pelo stdlib ``logging``: logs de bibliotecas (uvicorn,
sqlalchemy, httpx), ``warnings.warn`` e excecoes nao tratadas (``sys`` e
``threading``) saem no mesmo formato JSON. Os entrypoints chamam
``configure_logging`` antes de instanciar o Settings.

Redacao (rede de seguranca; a regra primaria e a allowlist do adapter SAP no
CLAUDE.md): roda como ULTIMO processor antes do JSON, depois do
``format_exc_info``, entao tambem cobre traceback. Recursiva em dict/list/tuple;
mascara o valor de qualquer chave que case com ``_PADRAO_CHAVE``
(case-insensitive) e qualquer ``Basic ...``/``Bearer ...`` dentro de strings.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from collections.abc import MutableMapping
from types import TracebackType
from typing import Any

import structlog

REDACTED = "***REDACTED***"

_PADRAO_CHAVE = re.compile(
    r"authorization|cookie|token|secret|pass|csrf|api[-_]?key|credential", re.IGNORECASE
)
_PADRAO_VALOR = re.compile(r"\b(basic|bearer)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_PROFUNDIDADE_MAX = 20
_NIVEIS = logging.getLevelNamesMapping()


def _chave_sensivel(chave: object) -> bool:
    return isinstance(chave, str) and _PADRAO_CHAVE.search(chave) is not None


def _mascarar(valor: Any, profundidade: int = 0) -> Any:
    if profundidade > _PROFUNDIDADE_MAX:
        return REDACTED
    if isinstance(valor, str):
        return _PADRAO_VALOR.sub(lambda m: f"{m.group(1)} {REDACTED}", valor)
    if isinstance(valor, dict):
        return {
            k: REDACTED if _chave_sensivel(k) else _mascarar(v, profundidade + 1)
            for k, v in valor.items()
        }
    if isinstance(valor, (list, tuple)):
        # par (nome, valor) no estilo httpx/requests: headers=[("authorization", "...")]
        if len(valor) == 2 and _chave_sensivel(valor[0]):
            par = [valor[0], REDACTED]
            return tuple(par) if isinstance(valor, tuple) else par
        itens = [_mascarar(v, profundidade + 1) for v in valor]
        return tuple(itens) if isinstance(valor, tuple) else itens
    return valor


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Processor structlog: mascara chaves sensiveis e credenciais em strings."""
    for chave in list(event_dict):
        event_dict[chave] = REDACTED if _chave_sensivel(chave) else _mascarar(event_dict[chave])
    return event_dict


def _instalar_excepthooks() -> None:
    log = structlog.get_logger("app.excepthook")

    def _sys_hook(
        tipo: type[BaseException], valor: BaseException, tb: TracebackType | None
    ) -> None:
        if issubclass(tipo, KeyboardInterrupt):
            sys.__excepthook__(tipo, valor, tb)
            return
        log.critical("excecao_nao_tratada", exc_info=(tipo, valor, tb))

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        log.critical(
            "excecao_nao_tratada_em_thread",
            thread=args.thread.name if args.thread is not None else None,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


def configure_logging(level: str = "INFO") -> None:
    """Configura structlog + stdlib para emitir JSON no stdout.

    Nivel invalido nao derruba o processo (o bootstrap roda antes do Settings):
    cai para INFO e loga ``log_level_invalido``; quem valida de fato e o Settings.
    """
    nome = level.strip().upper() if isinstance(level, str) else ""
    invalido = nome not in _NIVEIS or nome == "NOTSET"
    nivel = logging.INFO if invalido else _NIVEIS[nome]

    pre_chain: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
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
            redact_sensitive,  # depois do format_exc_info: pega o traceback
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
    _instalar_excepthooks()

    if invalido:
        structlog.get_logger(__name__).warning(
            "log_level_invalido", recebido=str(level)[:40], usado="INFO"
        )
