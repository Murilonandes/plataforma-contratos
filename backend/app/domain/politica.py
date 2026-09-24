"""Configuracao por organizacao de vendas (valor imutavel, sem I/O).

O dominio recebe a politica pronta; quem a carrega (settings/banco) e a camada de
aplicacao (Fase 3). ``POLITICA_PADRAO`` e o default enquanto a config real nao
existe: BRF1 so com BRL (``TODO(decisao #15)``: existe contrato em USD?).
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


POLITICA_PADRAO: Final[Mapping[str, PoliticaSalesOrg]] = MappingProxyType(
    {"BRF1": PoliticaSalesOrg(moedas=frozenset({"BRL"}))}
)
