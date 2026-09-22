"""Logging estruturado (structlog -> JSON no stdout).

Toda saida passa pelo stdlib ``logging``: logs de bibliotecas (uvicorn,
sqlalchemy, httpx), ``warnings.warn`` e excecoes nao tratadas (``sys``,
``threading`` e ``sys.unraisablehook``) saem no mesmo formato JSON. Os
entrypoints chamam ``configure_logging`` antes de instanciar o Settings.

Redacao: rede de seguranca. A regra primaria e a allowlist do adapter SAP no
CLAUDE.md; nao tente cobrir tudo aqui. Roda depois do ``format_exc_info``
(pega traceback), e recursiva em dict/Mapping/list/tuple/set, decodifica
bytes, converte objetos para ``repr`` antes de mascarar, mascara o valor de
chaves que casem com ``_PADRAO_CHAVE`` e trechos de credencial em strings
(``_PADROES_VALOR``). Se qualquer etapa falhar, o formatter emite so
``{"event": "log_render_error", "logger": ...}`` — nunca o evento cru.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from collections.abc import Callable, Mapping, MutableMapping
from types import TracebackType
from typing import Any

import structlog

REDACTED = "***REDACTED***"

_PADRAO_CHAVE = re.compile(
    r"authorization|cookie|token|secret|pass|csrf|api[-_]?key|credential|senha|pwd"
    r"|private[-_]?key|signature|mysapsso2|sap_sessionid|(?:^|[-_.])auth(?:$|[-_.])",
    re.IGNORECASE,
)

_PADROES_VALOR: tuple[tuple[re.Pattern[str], str], ...] = (
    # qualquer valor depois de "authorization" (com ou sem espaco, qualquer esquema)
    (
        re.compile(r"((?:proxy-)?authorization)([\"']?\s*[:=]\s*)[^\r\n]+", re.IGNORECASE),
        rf"\1\2{REDACTED}",
    ),
    (
        re.compile(r"\b(basic|bearer|digest|negotiate|ntlm)\s+\S+", re.IGNORECASE),
        rf"\1 {REDACTED}",
    ),
    # cookies de sessao/SSO do SAP
    (
        re.compile(r"\b(mysapsso2|sap_sessionid[\w-]*)=[^;\s,'\"&]+", re.IGNORECASE),
        rf"\1={REDACTED}",
    ),
    # userinfo em URL: https://user:senha@host
    (re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s@]+@", re.IGNORECASE), rf"\1{REDACTED}@"),
    # query com senha/token
    (
        re.compile(
            r"([?&](?:sap-password|password|access_token|token)=)[^&#\s'\"]+", re.IGNORECASE
        ),
        rf"\1{REDACTED}",
    ),
)

_PROFUNDIDADE_MAX = 20
_NIVEIS = logging.getLevelNamesMapping()


def _mascarar_texto(texto: str) -> str:
    for padrao, troca in _PADROES_VALOR:
        texto = padrao.sub(troca, texto)
    return texto


def _como_chave(chave: object) -> str | int | float | bool | None:
    """Chave aceita pelo json: str/int/float/bool/None; o resto vira texto."""
    if isinstance(chave, str):
        return _mascarar_texto(chave)
    if isinstance(chave, (bytes, bytearray)):
        return _mascarar_texto(bytes(chave).decode("utf-8", errors="replace"))
    if chave is None or isinstance(chave, (int, float, bool)):
        return chave
    return _mascarar_texto(repr(chave))


def _chave_sensivel(chave: object) -> bool:
    return isinstance(chave, str) and _PADRAO_CHAVE.search(chave) is not None


def _mascarar(valor: Any, profundidade: int = 0) -> Any:
    if profundidade > _PROFUNDIDADE_MAX:
        return REDACTED
    if valor is None or isinstance(valor, (bool, int, float)):
        return valor
    if isinstance(valor, str):
        return _mascarar_texto(valor)
    if isinstance(valor, (bytes, bytearray)):
        return _mascarar_texto(bytes(valor).decode("utf-8", errors="replace"))
    if isinstance(valor, Mapping):
        resultado: dict[Any, Any] = {}
        for k, v in valor.items():
            chave = _como_chave(k)
            resultado[chave] = (
                REDACTED if _chave_sensivel(chave) else _mascarar(v, profundidade + 1)
            )
        return resultado
    if isinstance(valor, (list, tuple, set, frozenset)):
        itens = list(valor)
        # par (nome, valor) no estilo httpx/requests: [("authorization", "...")]
        if (
            len(itens) == 2
            and isinstance(itens[0], (str, bytes, bytearray))
            and _chave_sensivel(_como_chave(itens[0]))
        ):
            return [_como_chave(itens[0]), REDACTED]
        return [_mascarar(v, profundidade + 1) for v in itens]
    return _mascarar_texto(repr(valor))


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Processor structlog: mascara chaves sensiveis e credenciais em qualquer valor."""
    for chave in list(event_dict):
        event_dict[chave] = REDACTED if _chave_sensivel(chave) else _mascarar(event_dict[chave])
    return event_dict


def _linha_de_erro_de_render(logger: str) -> str:
    return json.dumps({"event": "log_render_error", "logger": logger})


class _FormatterSeguro(structlog.stdlib.ProcessorFormatter):
    """Nenhuma falha de formatacao imprime o evento cru.

    Sem isso, uma excecao no pipeline (repr que quebra, ``%s`` sem argumento,
    chave nao serializavel) cai no ``Handler.handleError``, que joga a mensagem
    inteira, sem redacao, no stderr.
    """

    def format(self, record: logging.LogRecord) -> str:
        try:
            return super().format(record)
        except Exception:
            return _linha_de_erro_de_render(record.name)


def _repr_seguro(obj: object) -> str | None:
    if obj is None:
        return None
    try:
        return repr(obj)
    except Exception:
        return "<repr indisponivel>"


def _instalar_hooks() -> None:
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

    def _unraisable_hook(u: Any) -> None:
        # Excecao em __del__ / callback de GC: nao pode ser levantada.
        log.critical(
            "excecao_nao_levantavel",
            mensagem=u.err_msg or "",
            objeto=_repr_seguro(u.object),
            exc_info=(u.exc_type, u.exc_value, u.exc_traceback),
        )

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    hook_unraisable: Callable[[Any], None] = _unraisable_hook
    sys.unraisablehook = hook_unraisable


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

    formatter = _FormatterSeguro(
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
    _instalar_hooks()

    if invalido:
        structlog.get_logger(__name__).warning(
            "log_level_invalido", recebido=str(level)[:40], usado="INFO"
        )
