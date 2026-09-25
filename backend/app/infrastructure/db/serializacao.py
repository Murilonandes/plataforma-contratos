"""Serializacao para JSONB: snapshot do contrato (D6') e rascunho ``entrada``.

**Snapshot.** ``contrato_para_json`` gera o JSON canonico do ``Contract`` no
formato de ENTRADA do dominio (PascalCase, navegacoes ``to_*``), a partir das
``ESPECIFICACOES``:

- ``Edm.Decimal`` como string de escala fixa do campo (``"1.000"``,
  ``"1164.980000000"``), nunca ``float`` nem numero JSON;
- ``Edm.Date`` como ``YYYY-MM-DD``; data ``None`` tem a chave omitida;
- sem ``StatusBlock`` (e do mapper) e sem campos Computed.

``contrato_de_json`` faz o caminho de volta, reconvertendo pelas mesmas
especificacoes, e passa pelo ``Contract.criar``: o round-trip e exato
(``contrato_de_json(contrato_para_json(c)) == c``). Qualquer valor fora do
formato canonico e ``TypeError`` (snapshot corrompido nao vira contrato).

**Rascunho.** ``entrada_para_json`` guarda o rascunho recebido (qualquer
``Mapping``, possivelmente invalido) em JSON: ``Decimal`` -> texto exato,
``date`` -> ISO. Nao e reversivel por tipo (serve para auditoria/exibicao); a
Fase 3 define o formato de ida e volta do rascunho.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any, Final

from app.domain.contract import (
    CABECALHO,
    ITEM,
    PARCEIRO,
    PARCELA,
    PRECO,
    TEXTO,
    Campo,
    Contract,
    Tipo,
)
from app.domain.money import CONTEXTO_DECIMAL

# navegacao -> (especificacao, navegacoes filhas)
_Arvore = Mapping[str, tuple[tuple[Campo, ...], "Mapping[str, Any]"]]

_ITEM_FILHOS: Final[_Arvore] = {"to_PricingElement": (PRECO, {})}
_CABECALHO_FILHOS: Final[_Arvore] = {
    "to_Item": (ITEM, _ITEM_FILHOS),
    "to_Partner": (PARCEIRO, {}),
    "to_FormPag": (PARCELA, {}),
    "to_PricingElement": (PRECO, {}),
    "to_Text": (TEXTO, {}),
}
_ATRIBUTO_DA_NAVEGACAO: Final[Mapping[str, str]] = {
    "to_Item": "items",
    "to_Partner": "partners",
    "to_FormPag": "installments",
    "to_PricingElement": "pricing",
    "to_Text": "texts",
}


# ---- Snapshot: Contract -> JSON ---------------------------------------------------------


def _decimal_fixo(c: Campo, valor: Decimal) -> str:
    if c.escala is None:  # pragma: no cover - garantido pelas ESPECIFICACOES
        raise TypeError(f"{c.odata}: decimal sem escala")
    with localcontext(CONTEXTO_DECIMAL):
        return f"{valor.quantize(Decimal(1).scaleb(-c.escala)):f}"


def _entidade_para_json(obj: object, specs: tuple[Campo, ...]) -> dict[str, Any]:
    saida: dict[str, Any] = {}
    for c in specs:
        valor = getattr(obj, c.attr)
        if valor is None:
            continue  # data/decimal opcional ausente: chave omitida (Contract.criar le None)
        if c.tipo is Tipo.DECIMAL:
            saida[c.odata] = _decimal_fixo(c, valor)
        elif c.tipo is Tipo.DATA:
            saida[c.odata] = valor.isoformat()
        else:
            saida[c.odata] = valor
    return saida


def _com_filhos(obj: object, specs: tuple[Campo, ...], filhos: _Arvore) -> dict[str, Any]:
    saida = _entidade_para_json(obj, specs)
    for nav, (specs_filho, netos) in filhos.items():
        atributo = _ATRIBUTO_DA_NAVEGACAO[nav]
        saida[nav] = [_com_filhos(el, specs_filho, netos) for el in getattr(obj, atributo)]
    return saida


def contrato_para_json(contrato: Contract) -> dict[str, Any]:
    """JSON canonico do contrato (so tipos JSON: str, int, list, dict)."""
    saida = _entidade_para_json(contrato.header, CABECALHO)
    for nav, (specs, filhos) in _CABECALHO_FILHOS.items():
        elementos = getattr(contrato, _ATRIBUTO_DA_NAVEGACAO[nav])
        saida[nav] = [_com_filhos(el, specs, filhos) for el in elementos]
    return saida


# ---- Snapshot: JSON -> Contract ---------------------------------------------------------


def _valor_de_json(c: Campo, valor: object, path: str) -> object:
    if c.tipo is Tipo.DECIMAL:
        if not isinstance(valor, str):
            raise TypeError(f"snapshot: {path} fora do formato canonico")
        return Decimal(valor)
    if c.tipo is Tipo.DATA:
        if not isinstance(valor, str):
            raise TypeError(f"snapshot: {path} fora do formato canonico")
        return date.fromisoformat(valor)
    return valor


def _entidade_de_json(
    dados: Mapping[str, Any], specs: tuple[Campo, ...], filhos: _Arvore, prefixo: str
) -> dict[str, Any]:
    por_nome = {c.odata: c for c in specs}
    saida: dict[str, Any] = {}
    for chave, valor in dados.items():
        path = f"{prefixo}{chave}"
        if chave in por_nome:
            saida[chave] = _valor_de_json(por_nome[chave], valor, path)
        elif chave in filhos and isinstance(valor, list):
            specs_filho, netos = filhos[chave]
            saida[chave] = [
                _entidade_de_json(el, specs_filho, netos, f"{path}[{i}].")
                for i, el in enumerate(valor)
            ]
        else:
            saida[chave] = valor  # o Contract.criar decide (unknown_field etc.)
    return saida


def contrato_de_json(dados: Mapping[str, Any]) -> Contract:
    """Volta do JSON canonico; valida tudo pelo ``Contract.criar``."""
    return Contract.criar(_entidade_de_json(dados, CABECALHO, _CABECALHO_FILHOS, ""))


# ---- Rascunho (entrada) -----------------------------------------------------------------


def entrada_para_json(entrada: Mapping[str, object]) -> dict[str, Any]:
    """Rascunho -> JSON: ``Decimal`` e ``date`` viram texto; o resto so se for JSON puro."""
    resultado = _json_seguro(entrada)
    if not isinstance(resultado, dict):  # pragma: no cover - Mapping sempre vira dict
        raise TypeError("entrada precisa ser um Mapping")
    return resultado


def _json_seguro(valor: object) -> object:
    if valor is None or isinstance(valor, (str, bool)) or type(valor) is int:
        return valor
    if isinstance(valor, Decimal):
        if not valor.is_finite():
            raise TypeError("entrada: decimal nao finito")
        return f"{valor:f}"
    if type(valor) is date:  # datetime (subclasse de date) nao e Edm.Date
        return valor.isoformat()
    if isinstance(valor, Mapping):
        if not all(isinstance(k, str) for k in valor):
            raise TypeError("entrada: chave de objeto precisa ser str")
        return {k: _json_seguro(v) for k, v in valor.items()}
    if isinstance(valor, Sequence) and not isinstance(valor, (bytes, bytearray)):
        return [_json_seguro(v) for v in valor]
    if isinstance(valor, datetime):
        raise TypeError("entrada: datetime nao e aceito (use date)")
    raise TypeError(f"entrada: tipo sem representacao segura ({type(valor).__name__})")
