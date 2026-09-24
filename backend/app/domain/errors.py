"""Erros do dominio.

Contrato consumido pelo front (via API), por isso estavel:

- ``FieldError``: ``path`` no formato do payload OData (PascalCase e
  navegacao ``to_*``, indice base 0: ``to_Item[1].Material`` e o 2o item),
  ``code`` estavel em snake_case, ``message`` em PT-BR gerada a partir do code
  e ``params`` JSON-safe (ex.: ``{"max": 40}``). O front mapeia por
  ``code`` + ``path``, nunca pelo texto.
- ``ErrorCode`` + ``MENSAGENS``: a lista unica de codes, cada um com o seu
  template e os params que exige. Code novo entra aqui e em nenhum outro lugar.
- ``ErrorCollector``: a validacao ACUMULA todos os erros (contrato inteiro,
  incluindo filhos com prefixo de path) e levanta ``DomainValidationError``
  uma vez, no fim — nunca no primeiro erro.
- ``InvalidTransitionError``: transicao fora da matriz (ARCHITECTURE §4).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from types import MappingProxyType
from typing import Final

from app.domain.enums import ContractStatus, TransitionEvent

ParamValor = str | int | bool | None
_SUFIXO_INDICE = re.compile(r"\[\d+\]$")


@unique
class ErrorCode(StrEnum):
    """Codes estaveis (valor == nome em minusculo). Nunca renomear um code publicado."""

    REQUIRED = "required"
    MAX_LENGTH = "max_length"
    NOT_DECIMAL = "not_decimal"
    NOT_FINITE = "not_finite"
    DECIMAL_SCALE = "decimal_scale"
    DECIMAL_PRECISION = "decimal_precision"
    MUST_BE_POSITIVE = "must_be_positive"
    MIN_ITEMS = "min_items"
    LENGTH_MISMATCH = "length_mismatch"
    INVALID_WEIGHT = "invalid_weight"
    MAX_ITEMS = "max_items"
    DATES_NOT_INCREASING = "dates_not_increasing"
    INSTALLMENT_BELOW_MINIMUM = "installment_below_minimum"
    MIN_LENGTH = "min_length"
    ACTOR_NOT_ALLOWED = "actor_not_allowed"
    INVALID_FORMAT = "invalid_format"
    NOT_APPLICABLE = "not_applicable"
    INSTALLMENT_OUT_OF_SEQUENCE = "installment_out_of_sequence"
    INSTALLMENT_PERCENT_SUM = "installment_percent_sum"
    CURRENCY_NOT_ALLOWED = "currency_not_allowed"
    CURRENCY_MISMATCH = "currency_mismatch"
    SALES_ORG_NOT_CONFIGURED = "sales_org_not_configured"
    INVALID_CHARACTERS = "invalid_characters"
    DUPLICATE_PARTNER_FUNCTION = "duplicate_partner_function"
    PARTNER_IDENTIFIER_REQUIRED = "partner_identifier_required"
    UNKNOWN_FIELD = "unknown_field"
    INVALID_TYPE = "invalid_type"


# code -> (template da mensagem em PT-BR, params exigidos). ``{campo}`` e o
# ultimo segmento do path SEM indice (``to_Item[1]`` -> ``to_Item``): mensagem
# nunca cita posicao base 0; o front posiciona pelo ``path``.
MENSAGENS: Final[Mapping[ErrorCode, tuple[str, tuple[str, ...]]]] = MappingProxyType(
    {
        ErrorCode.REQUIRED: ("campo '{campo}' e obrigatorio", ()),
        ErrorCode.MAX_LENGTH: ("campo '{campo}' excede {max} caracteres", ("max",)),
        ErrorCode.NOT_DECIMAL: ("campo '{campo}' precisa ser Decimal, nunca float", ()),
        ErrorCode.NOT_FINITE: ("campo '{campo}' precisa ser finito (sem NaN/Infinity)", ()),
        ErrorCode.DECIMAL_SCALE: (
            "campo '{campo}' aceita no maximo {max} casas decimais",
            ("max",),
        ),
        ErrorCode.DECIMAL_PRECISION: (
            "campo '{campo}' aceita no maximo {max} digitos antes da virgula",
            ("max",),
        ),
        ErrorCode.MUST_BE_POSITIVE: ("campo '{campo}' deve ser maior que zero", ()),
        ErrorCode.MIN_ITEMS: ("campo '{campo}' precisa de ao menos {min} elemento(s)", ("min",)),
        ErrorCode.LENGTH_MISMATCH: (
            "campo '{campo}' deve ter {esperado} elemento(s), tem {recebido}",
            ("esperado", "recebido"),
        ),
        ErrorCode.INVALID_WEIGHT: ("campo '{campo}' deve ser inteiro entre 1 e {max}", ("max",)),
        ErrorCode.MAX_ITEMS: ("campo '{campo}' aceita no maximo {max} elemento(s)", ("max",)),
        ErrorCode.DATES_NOT_INCREASING: (
            "campo '{campo}' deve ser posterior a data da parcela anterior",
            (),
        ),
        ErrorCode.INSTALLMENT_BELOW_MINIMUM: (
            "total insuficiente para {parcelas} parcela(s): cada parcela precisa de ao menos "
            "0.01; aumente o total para ao menos {minimo_total} ou equilibre os pesos",
            ("minimo_total", "parcelas"),
        ),
        ErrorCode.MIN_LENGTH: ("campo '{campo}' precisa de ao menos {min} caracteres", ("min",)),
        ErrorCode.ACTOR_NOT_ALLOWED: (
            "ator '{recebido}' nao pode disparar esta transicao (esperado '{esperado}')",
            ("esperado", "recebido"),
        ),
        ErrorCode.INVALID_FORMAT: ("campo '{campo}' fora do formato: {formato}", ("formato",)),
        ErrorCode.NOT_APPLICABLE: ("campo '{campo}' nao se aplica a esta transicao", ()),
        ErrorCode.INSTALLMENT_OUT_OF_SEQUENCE: (
            "campo '{campo}' deve ser {esperado} (parcelas numeradas de 1 a N, na ordem)",
            ("esperado",),
        ),
        ErrorCode.INSTALLMENT_PERCENT_SUM: (
            "a soma das porcentagens das parcelas deve ser 100.0000, e {soma}",
            ("soma",),
        ),
        ErrorCode.CURRENCY_NOT_ALLOWED: (
            "moeda '{moeda}' nao permitida para a organizacao de vendas (permitidas: {permitidas})",
            ("moeda", "permitidas"),
        ),
        ErrorCode.CURRENCY_MISMATCH: (
            "campo '{campo}' deve ser '{esperado}' (moeda do cabecalho), veio '{recebido}'",
            ("esperado", "recebido"),
        ),
        ErrorCode.SALES_ORG_NOT_CONFIGURED: (
            "organizacao de vendas '{sales_org}' sem configuracao na plataforma",
            ("sales_org",),
        ),
        ErrorCode.INVALID_CHARACTERS: (
            "campo '{campo}' contem caracteres invalidos (controle ou surrogate)",
            (),
        ),
        ErrorCode.DUPLICATE_PARTNER_FUNCTION: (
            "parceiro duplicado para funcao '{funcao}'",
            ("funcao",),
        ),
        ErrorCode.PARTNER_IDENTIFIER_REQUIRED: (
            "campo '{campo}' precisa de ao menos um identificador de parceiro",
            (),
        ),
        ErrorCode.UNKNOWN_FIELD: ("campo '{campo}' nao e aceito", ()),
        ErrorCode.INVALID_TYPE: ("campo '{campo}' deve ser {tipo}", ("tipo",)),
    }
)


# ---- Paths -------------------------------------------------------------------


def campo(prefixo: str, nome: str) -> str:
    """``campo("to_Item[1]", "Material")`` -> ``"to_Item[1].Material"``."""
    return f"{prefixo}.{nome}" if prefixo else nome


def indice(navegacao: str, i: int) -> str:
    """``indice("to_Item", 1)`` -> ``"to_Item[1]"`` (base 0)."""
    if i < 0:
        raise ValueError(f"indice precisa ser >= 0, veio {i}")
    return f"{navegacao}[{i}]"


# ---- FieldError --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldError:
    path: str
    code: ErrorCode
    message: str
    params: Mapping[str, ParamValor]

    @classmethod
    def criar(cls, path: str, code: ErrorCode, **params: ParamValor) -> FieldError:
        """Monta o erro; a mensagem sai do template do code (unica fonte)."""
        template, esperados = MENSAGENS[code]
        if sorted(params) != sorted(esperados):
            raise ValueError(
                f"params de '{code.value}' deveriam ser {sorted(esperados)}, "
                f"vieram {sorted(params)}"
            )
        for nome, valor in params.items():
            if valor is not None and not isinstance(valor, (str, int, bool)):
                raise TypeError(f"param '{nome}' precisa ser str, int, bool ou None")
        nome_campo = _SUFIXO_INDICE.sub("", path.rsplit(".", 1)[-1])
        mensagem = template.format(campo=nome_campo, **params)
        return cls(path=path, code=code, message=mensagem, params=MappingProxyType(dict(params)))

    def com_prefixo(self, prefixo: str) -> FieldError:
        return FieldError(
            path=campo(prefixo, self.path), code=self.code, message=self.message, params=self.params
        )


# ---- Excecoes ----------------------------------------------------------------


class DomainError(Exception):
    """Base dos erros de dominio."""


class DomainValidationError(DomainError):
    """Todos os erros de validacao do contrato, levantados de uma vez."""

    def __init__(self, errors: Iterable[FieldError]) -> None:
        self.errors: tuple[FieldError, ...] = tuple(errors)
        if not self.errors:
            raise ValueError("DomainValidationError precisa de ao menos um erro")
        detalhe = "; ".join(f"{e.path}: {e.message}" for e in self.errors)
        super().__init__(f"{len(self.errors)} erro(s) de validacao: {detalhe}")


class InvalidTransitionError(DomainError):
    """Par (estado, evento) fora da matriz de transicoes."""

    def __init__(self, from_status: ContractStatus, event: TransitionEvent) -> None:
        self.from_status = from_status
        self.event = event
        super().__init__(
            f"transicao invalida: evento '{event.value}' nao e permitido "
            f"no estado '{from_status.value}'"
        )


# ---- Acumulador --------------------------------------------------------------


class ErrorCollector:
    """Acumula ``FieldError`` durante a validacao; levanta uma vez no fim."""

    def __init__(self) -> None:
        self._erros: list[FieldError] = []

    def adicionar(self, path: str, code: ErrorCode, **params: ParamValor) -> None:
        self._erros.append(FieldError.criar(path, code, **params))

    def incorporar(self, erros: Iterable[FieldError], *, prefixo: str) -> None:
        """Traz erros de um filho (VO/lista) reescrevendo o path com o prefixo."""
        self._erros.extend(e.com_prefixo(prefixo) for e in erros)

    @property
    def erros(self) -> tuple[FieldError, ...]:
        return tuple(self._erros)

    def __bool__(self) -> bool:
        return bool(self._erros)

    def tem_erro(self, path: str) -> bool:
        """Ja existe erro exatamente neste path (regras nao empilham erro sobre ele)."""
        return any(e.path == path for e in self._erros)

    def levantar_se_houver(self) -> None:
        if self._erros:
            raise DomainValidationError(self._erros)
