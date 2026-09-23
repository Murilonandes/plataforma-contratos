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
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.errors import ErrorCode, FieldError, campo


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
