"""Configuracao por organizacao de vendas (valor imutavel, sem I/O).

O dominio recebe a politica pronta; quem a carrega (settings/banco) e a camada de
aplicacao (Fase 3). ``POLITICA_PADRAO`` e o default enquanto a config real nao
existe: BRF1 so com BRL (``TODO(decisao #15)``: existe contrato em USD?),
parcelas obrigatorias na condicao Z999 e ``Data`` obrigatoria em toda parcela
(``TODO(decisao #16)``: contrato sem parcelas existe?).
Os valores permitidos de codigos SAP (``TODO(decisao #11)``) e os
``required_fields`` (``TODO(decisao #10)``) entram aqui na Fase 3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class PoliticaSalesOrg:
    moedas: frozenset[str]
    # Obrigatorio de negocio (o SAP aceita sem): condicoes de pagamento
    # (CustomerPaymentTerms) que exigem ao menos uma parcela em to_FormPag, e se
    # toda parcela precisa de Data. TODO(decisao #16).
    condicoes_com_parcelas: frozenset[str] = frozenset()
    data_da_parcela_obrigatoria: bool = False


POLITICA_PADRAO: Final[Mapping[str, PoliticaSalesOrg]] = MappingProxyType(
    {
        "BRF1": PoliticaSalesOrg(
            moedas=frozenset({"BRL"}),
            condicoes_com_parcelas=frozenset({"Z999"}),
            data_da_parcela_obrigatoria=True,
        )
    }
)
