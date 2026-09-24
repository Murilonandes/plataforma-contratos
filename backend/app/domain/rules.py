"""Regras de negocio inter-campo do contrato.

Roda sobre dados ja validados campo a campo (tipo, MaxLength, strip) e so cobre
invariantes que envolvem mais de um campo ou elemento. O ``Contract.criar``
chama ``validar_regras`` com os erros do mesmo ``ErrorCollector``: o contrato
continua levantando UMA vez, com erros de campo e de regra juntos.

- ``to_Item`` precisa de ao menos um item (``min_items``). Se ``to_Item`` nem
  e lista, o erro de tipo ja foi dado e a regra nao empilha outro.
- No maximo um parceiro por ``PartnerFunction`` (ARCHITECTURE §8). Toda
  repeticao depois da primeira e erro, no path do elemento repetido. Funcao
  vazia ja e ``required`` e fica de fora. Comparacao exata: ``PartnerFunction``
  nao e ``IsUpperCase`` no metadata, entao o dominio nao normaliza caixa.
- ``to_FormPag`` (``validar_parcelas``): ``calcular_parcelas`` e o unico jeito de
  montar as parcelas, mas o dominio nao confia nisso. Ate ``MAX_PARCELAS``
  (``max_items``); ``Parcela`` = 1..N na ordem (``installment_out_of_sequence``);
  soma das ``Porcentagem`` = ``100.0000`` (``installment_percent_sum``); datas
  presentes estritamente crescentes (``dates_not_increasing``, ``TODO(decisao #12)``).
  Presenca e sinal de ``Porcentagem``/``Valor`` ficam no VO (``nao_nulo``, ``positivo``).
  Campo com erro proprio (``None`` aqui) nao entra nas regras, para nao empilhar erro.
- Moeda (``validar_moedas``): a do cabecalho precisa estar na politica da sales
  org (``currency_not_allowed``; sales org sem politica: ``sales_org_not_configured``),
  e itens e parcelas usam a mesma (``currency_mismatch``). Moeda vazia no item
  herda a do cabecalho (nao e Mandatory no metadata). ``TODO(decisao #15)``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, localcontext
from typing import Final

from app.domain.errors import ErrorCode, FieldError, campo
from app.domain.money import CONTEXTO_DECIMAL
from app.domain.politica import PoliticaSalesOrg

MAX_PARCELAS: Final = 36  # tambem o limite de calcular_parcelas (installments.py)
_CEM_POR_CENTO: Final = Decimal("100.0000")


def validar_regras(
    *, parceiros: Sequence[tuple[str, str]], itens_recebidos: int | None
) -> tuple[FieldError, ...]:
    """``parceiros``: ``(path do elemento, PartnerFunction)`` na ordem do payload.

    ``itens_recebidos``: quantos elementos vieram em ``to_Item`` (validos ou
    nao); ``None`` quando ``to_Item`` nao e lista.
    """
    erros: list[FieldError] = []
    if itens_recebidos == 0:
        erros.append(FieldError.criar("to_Item", ErrorCode.MIN_ITEMS, min=1))

    vistas: set[str] = set()
    for path, funcao in parceiros:
        if not funcao:
            continue
        if funcao in vistas:
            erros.append(
                FieldError.criar(
                    campo(path, "PartnerFunction"),
                    ErrorCode.DUPLICATE_PARTNER_FUNCTION,
                    funcao=funcao,
                )
            )
        vistas.add(funcao)
    return tuple(erros)


def validar_parcelas(
    parcelas: Sequence[tuple[str, int | None, Decimal | None, date | None]],
    *,
    recebidas: int | None,
) -> tuple[FieldError, ...]:
    """``parcelas``: ``(path, Parcela, Porcentagem, Data)`` ja validados campo a campo.

    ``recebidas``: quantos elementos vieram em ``to_FormPag`` (validos ou nao).
    """
    erros: list[FieldError] = []
    if recebidas is not None and recebidas > MAX_PARCELAS:
        erros.append(FieldError.criar("to_FormPag", ErrorCode.MAX_ITEMS, max=MAX_PARCELAS))

    # Sequencia so faz sentido se todo elemento recebido virou parcela (senao o
    # indice nao bate com a posicao e o elemento invalido ja deu erro).
    if recebidas == len(parcelas):
        for i, (path, numero, _, _) in enumerate(parcelas):
            if numero is not None and numero != i + 1:
                erros.append(
                    FieldError.criar(
                        campo(path, "Parcela"),
                        ErrorCode.INSTALLMENT_OUT_OF_SEQUENCE,
                        esperado=i + 1,
                    )
                )

    porcentagens = [pct for _, _, pct, _ in parcelas if pct is not None]
    if parcelas and len(porcentagens) == len(parcelas):
        with localcontext(CONTEXTO_DECIMAL):
            soma = sum(porcentagens, Decimal("0.0000"))
        if soma != _CEM_POR_CENTO:
            erros.append(
                FieldError.criar("to_FormPag", ErrorCode.INSTALLMENT_PERCENT_SUM, soma=f"{soma:f}")
            )

    anterior: date | None = None
    for path, _, _, data in parcelas:
        if anterior is not None and data is not None and data <= anterior:
            erros.append(FieldError.criar(campo(path, "Data"), ErrorCode.DATES_NOT_INCREASING))
        anterior = data
    return tuple(erros)


def validar_moedas(
    *,
    sales_org: str,
    moeda: str,
    outras: Sequence[tuple[str, str]],
    politica: Mapping[str, PoliticaSalesOrg],
) -> tuple[FieldError, ...]:
    """``outras``: ``(path do elemento, TransactionCurrency)`` de itens e parcelas."""
    erros: list[FieldError] = []
    config = politica.get(sales_org)
    if sales_org and config is None:
        erros.append(
            FieldError.criar(
                "SalesOrganization", ErrorCode.SALES_ORG_NOT_CONFIGURED, sales_org=sales_org
            )
        )
    elif config is not None and moeda and moeda not in config.moedas:
        erros.append(
            FieldError.criar(
                "TransactionCurrency",
                ErrorCode.CURRENCY_NOT_ALLOWED,
                moeda=moeda,
                permitidas=", ".join(sorted(config.moedas)),
            )
        )
    if moeda:
        for path, outra in outras:
            if outra and outra != moeda:
                erros.append(
                    FieldError.criar(
                        campo(path, "TransactionCurrency"),
                        ErrorCode.CURRENCY_MISMATCH,
                        esperado=moeda,
                        recebido=outra,
                    )
                )
    return tuple(erros)
