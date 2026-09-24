"""Contrato do dominio -> payload OData do ``CriaContrato`` (funcoes puras, sem I/O).

``to_payload`` e gerado a partir das ``ESPECIFICACOES`` do dominio, que espelham
o ``$metadata`` inclusive na ordem: as chaves saem na ordem do metadata
(propriedades, ``StatusBlock`` no lugar dele, depois as navegacoes).

- ``StatusBlock = "06"`` SEMPRE (contrato nasce bloqueado; omitir = desbloqueado).
- Campos Computed (``SalesContract``, ``SalesContractItem``, ``ConditionUUID``) e
  as navegacoes de volta (``_Contract``, ``_Item``) nunca saem.
- ``Edm.String`` vazio sai ``""``; ``Edm.Date`` ``None`` tem a chave OMITIDA
  (``TODO(decisao #9)``); presente sai ``YYYY-MM-DD``. ``Int32`` sai ``int``.
- ``Edm.Decimal`` sai com a escala do campo FIXADA (``Valor`` ``7766.50``, nunca
  ``7766.5`` nem ``7.7665E+3``), sem arredondar: casa a mais e ``ValueError``.
  ``decimal_as_string`` (``TODO(decisao #4)``, a flag vem por parametro, o mapper
  nao le settings): ``True`` -> ``f"{d:f}"``; ``False`` -> ``Decimal`` ja
  quantizado, escrito como literal exato por ``to_json``. Nunca ``str(Decimal)``,
  nunca ``float``. ``None`` em decimal e ``ValueError`` (segunda barreira: o
  ``Contract.criar`` ja exige; ``Porcentagem``/``Valor`` vem de ``calcular_parcelas``).

``to_json`` serializa o payload em ``bytes`` compactos. O adapter SAP envia
``content=to_json(...)``, nunca ``json=`` do httpx (que passaria por ``float``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from functools import partial
from typing import Any, Final

from app.domain.contract import (
    CABECALHO,
    ESPECIFICACOES,
    ITEM,
    PARCEIRO,
    PARCELA,
    PRECO,
    TEXTO,
    Campo,
    Contract,
    Tipo,
)
from app.domain.money import casas_decimais, sem_zero_negativo

STATUS_BLOCK: Final = "06"

# Escala de cada Edm.Decimal (mesma em toda entidade onde o campo aparece) e o
# quantum correspondente (``quantize`` usa so o expoente: 1E-2, 1E-9...).
_ESCALAS: Final[Mapping[str, int]] = {
    c.odata: c.escala for specs in ESPECIFICACOES.values() for c in specs if c.escala is not None
}
_QUANTUNS: Final[Mapping[str, Decimal]] = {
    nome: Decimal(1).scaleb(-escala) for nome, escala in _ESCALAS.items()
}


def to_payload(contract: Contract, *, decimal_as_string: bool) -> dict[str, Any]:
    ent = partial(_entidade, como_string=decimal_as_string)  # a flag entra num lugar so
    payload = ent(contract.header, CABECALHO)
    payload["StatusBlock"] = STATUS_BLOCK
    # Navegacoes na ordem do metadata (CriaContratoType).
    payload["to_FormPag"] = [ent(p, PARCELA) for p in contract.installments]
    payload["to_Item"] = [
        {**ent(i, ITEM), "to_PricingElement": [ent(p, PRECO) for p in i.pricing]}
        for i in contract.items
    ]
    payload["to_Partner"] = [ent(p, PARCEIRO) for p in contract.partners]
    payload["to_PricingElement"] = [ent(p, PRECO) for p in contract.pricing]
    payload["to_Text"] = [ent(t, TEXTO) for t in contract.texts]
    return payload


def _entidade(obj: object, specs: tuple[Campo, ...], *, como_string: bool) -> dict[str, Any]:
    saida: dict[str, Any] = {}
    for c in specs:
        valor = getattr(obj, c.attr)
        if c.tipo is Tipo.DATA:
            if valor is not None:
                saida[c.odata] = valor.isoformat()
        elif c.tipo is Tipo.DECIMAL:
            saida[c.odata] = _decimal(c, valor, como_string)
        else:
            saida[c.odata] = valor
    return saida


def _decimal(c: Campo, valor: object, como_string: bool) -> Decimal | str:
    if not isinstance(valor, Decimal) or not valor.is_finite():
        raise ValueError(f"{c.odata}: decimal ausente ou invalido")
    valor = sem_zero_negativo(valor)  # -0 -> 0, antes de validar (mesma regra do dominio)
    if casas_decimais(valor) > _ESCALAS[c.odata]:
        raise ValueError(f"{c.odata}: {valor} tem mais de {_ESCALAS[c.odata]} casas decimais")
    fixo = valor.quantize(_QUANTUNS[c.odata])  # exato: cabe na escala
    return f"{fixo:f}" if como_string else fixo  # ponto fixo, nunca str(Decimal)


def to_json(payload: Mapping[str, Any]) -> bytes:
    """JSON compacto em UTF-8 (ASCII com escapes). Decimal sai como literal exato."""
    return _json(payload).encode()  # UTF-8; o texto ja e ASCII (json.dumps escapa)


def _json(valor: object) -> str:
    if isinstance(valor, Mapping):
        return "{" + ",".join(_membro(k, v) for k, v in valor.items()) + "}"
    if isinstance(valor, list):
        return "[" + ",".join(_json(v) for v in valor) + "]"
    if isinstance(valor, Decimal):
        if not valor.is_finite():
            raise ValueError("to_json: decimal nao finito")
        return f"{valor:f}"
    if isinstance(valor, str) or type(valor) is int:  # bool e subclasse de int: recusado
        return json.dumps(valor)
    raise TypeError(f"to_json: tipo nao suportado ({type(valor).__name__})")


def _membro(chave: object, valor: object) -> str:
    if not isinstance(chave, str):
        raise TypeError("to_json: chave precisa ser str")
    return json.dumps(chave) + ":" + _json(valor)
