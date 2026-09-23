"""Calculo das parcelas (``to_FormPag``) pelo metodo do maior resto.

``to_FormPag`` NUNCA vem do cliente: a entrada e ``total`` + ``pesos`` +
``datas`` (+ ``FormPag``, que o caso de uso poe em cada parcela) e as parcelas
sao sempre montadas aqui (CLAUDE.md, ARCHITECTURE §8).

Maior resto (Hare), aplicado de forma independente para ``Porcentagem`` (alvo
``100.0000``, unidade ``0.0001``) e ``Valor`` (alvo ``total``, unidade
``0.01``). Tudo em unidades inteiras, sem divisao ``Decimal``:

1. cota exata da parcela i = ``alvo * peso_i / soma_pesos``;
2. base = piso da cota (``divmod``); resto = numerador do fracionario;
3. residuo = ``alvo - soma(bases)`` unidades (sempre ``< n``);
4. ordena os indices pela chave ``(-resto, indice)``: maior resto primeiro,
   empate vai para o menor indice; os ``residuo`` primeiros ganham 1 unidade.

Com pesos iguais todos os restos empatam e o residuo cai nas primeiras parcelas.

Validacao acumulada (uma ``DomainValidationError``), paths da entrada da API:
``total`` (Decimal > 0, 2 casas, 13 digitos inteiros: espelho de ``Valor``),
``pesos`` (1 a ``MAX_PARCELAS`` inteiros entre 1 e ``PESO_MAX``), ``datas``
(mesmo tamanho, ``date``, estritamente crescentes: ``TODO(decisao #12)``).

Minimo por parcela pela COTA EXATA: toda parcela precisa de cota ``>= 0.01``,
ou seja ``total >= 0.01 * soma_pesos / menor_peso``. A regra e monotonica
(qualquer total acima do minimo passa), entao ``minimo_total`` e exato. Com
``PESO_MAX`` e ``MAX_PARCELAS`` a menor porcentagem e ``>= 100 / 350001``
(~``0.0003``): a porcentagem nunca fica abaixo de ``0.0001``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from app.domain.contract import PARCELA, validar_campo
from app.domain.errors import ErrorCode, ErrorCollector, indice

MAX_PARCELAS: Final = 36
PESO_MAX: Final = 10_000

_ESCALA_PCT: Final = 4
_ESCALA_BRL: Final = 2
_CEM_POR_CENTO: Final = 100 * 10**_ESCALA_PCT  # 100.0000 em unidades de 0.0001

# ``total`` segue a especificacao de ``Valor`` (metadata), obrigatorio e > 0.
# O path do erro e ``total`` (passado na chamada), nao o ``odata`` da especificacao.
_TOTAL: Final = dataclasses.replace(
    next(c for c in PARCELA if c.odata == "Valor"), obrigatorio=True, positivo=True
)


@dataclass(frozen=True, slots=True)
class ParcelaCalculada:
    parcela: int  # 1..N
    porcentagem: Decimal  # 4 casas
    valor: Decimal  # 2 casas
    data: date  # data base (ZFBDT), nao vencimento


def calcular_parcelas(
    total: Decimal, pesos: Sequence[int], datas: Sequence[date]
) -> tuple[ParcelaCalculada, ...]:
    col = ErrorCollector()
    validar_campo(_TOTAL, total, "total", col)
    pesos_ok = _validar_pesos(pesos, col)
    _validar_datas(datas, len(pesos) if pesos_ok else None, col)
    col.levantar_se_houver()  # daqui em diante: total Decimal valido, pesos e datas validos

    centavos = int(total.scaleb(_ESCALA_BRL))  # exato: no maximo 2 casas (validado)
    soma, menor = sum(pesos), min(pesos)
    if centavos * menor < soma:  # cota exata da menor parcela < 0.01
        minimo = -(-soma // menor)  # teto, em centavos
        col.adicionar(
            "total",
            ErrorCode.INSTALLMENT_BELOW_MINIMUM,
            minimo_total=str(Decimal(minimo).scaleb(-_ESCALA_BRL)),
            parcelas=len(pesos),
        )
        col.levantar_se_houver()

    pcts = _maior_resto(_CEM_POR_CENTO, pesos)
    valores = _maior_resto(centavos, pesos)
    return tuple(
        ParcelaCalculada(
            parcela=i + 1,
            porcentagem=Decimal(pcts[i]).scaleb(-_ESCALA_PCT),
            valor=Decimal(valores[i]).scaleb(-_ESCALA_BRL),
            data=datas[i],
        )
        for i in range(len(pesos))
    )


def _maior_resto(alvo: int, pesos: Sequence[int]) -> list[int]:
    """Distribui ``alvo`` unidades proporcionalmente a ``pesos``; soma exata."""
    soma = sum(pesos)
    bases: list[int] = []
    restos: list[int] = []
    for w in pesos:
        base, resto = divmod(alvo * w, soma)
        bases.append(base)
        restos.append(resto)
    residuo = alvo - sum(bases)
    ordem = sorted(range(len(pesos)), key=lambda i: (-restos[i], i))
    for i in ordem[:residuo]:
        bases[i] += 1
    return bases


def _validar_pesos(pesos: object, col: ErrorCollector) -> bool:
    if not isinstance(pesos, list | tuple):
        col.adicionar("pesos", ErrorCode.INVALID_TYPE, tipo="lista")
        return False
    if not pesos:
        col.adicionar("pesos", ErrorCode.MIN_ITEMS, min=1)
    elif len(pesos) > MAX_PARCELAS:
        col.adicionar("pesos", ErrorCode.MAX_ITEMS, max=MAX_PARCELAS)
    for i, w in enumerate(pesos):
        if type(w) is not int or not 1 <= w <= PESO_MAX:  # bool e subclasse de int
            col.adicionar(indice("pesos", i), ErrorCode.INVALID_WEIGHT, max=PESO_MAX)
    return True


def _validar_datas(datas: object, esperado: int | None, col: ErrorCollector) -> None:
    if not isinstance(datas, list | tuple):
        col.adicionar("datas", ErrorCode.INVALID_TYPE, tipo="lista")
        return
    if esperado is not None and len(datas) != esperado:
        col.adicionar("datas", ErrorCode.LENGTH_MISMATCH, esperado=esperado, recebido=len(datas))
    anterior: date | None = None
    for i, d in enumerate(datas):
        if type(d) is not date:  # datetime e subclasse de date
            col.adicionar(indice("datas", i), ErrorCode.INVALID_TYPE, tipo="data")
            anterior = None
            continue
        # TODO(decisao #12): datas estritamente crescentes ate a SD/Comercial confirmarem
        if anterior is not None and d <= anterior:
            col.adicionar(indice("datas", i), ErrorCode.DATES_NOT_INCREASING)
        anterior = d
