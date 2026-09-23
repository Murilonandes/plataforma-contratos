"""Leitura das referencias SAP de ``docs/sap`` para os testes do dominio.

- ``metadata()``: le ``metadata.xml``. O arquivo do repo tem ``&`` sem escape em
  anotacoes ``DocumentationRef`` (nao e XML bem-formado); escapamos so os ``&``
  soltos antes de parsear, sem alterar a fonte da verdade.
- ``payload_exemplo_como_entrada()``: le ``payload_exemplo.json`` convertendo
  para os tipos do dominio (``Decimal``, ``date``), como a API fara na Fase 3,
  e tira ``StatusBlock`` (o exemplo e a SAIDA do mapper; a entrada do dominio
  nao aceita esse campo).
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

_EDM = "{http://docs.oasis-open.org/odata/ns/edm}"
_AMP_SOLTO = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")

_DECIMAIS = {"RequestedQuantity", "ConditionRateValue", "Porcentagem", "Valor"}
_DATAS = {
    "CustomerPurchaseOrderDate",
    "SalesContractValidityEndDate",
    "ScheduleDate",
    "ScheduleDate2",
    "Data",
}


def _docs_sap() -> Path:
    for pasta in Path(__file__).resolve().parents:
        candidato = pasta / "docs" / "sap"
        if (candidato / "metadata.xml").is_file():
            return candidato
    raise FileNotFoundError("docs/sap/metadata.xml nao encontrado acima de " + __file__)


@dataclass(frozen=True)
class PropriedadeMeta:
    nome: str
    tipo: str  # "String", "Decimal", "Date", "Int32", "Guid"...
    max_length: int | None
    precision: int | None
    scale: str | None  # "3", "9", "variable"
    anotacoes: frozenset[str] = field(default_factory=frozenset)

    @property
    def mandatory(self) -> bool:
        return "FieldControl=Mandatory" in self.anotacoes

    @property
    def upper(self) -> bool:
        return "IsUpperCase" in self.anotacoes

    @property
    def computed(self) -> bool:
        return "Computed" in self.anotacoes


@cache
def metadata() -> dict[str, dict[str, PropriedadeMeta]]:
    """EntityType -> propriedade -> PropriedadeMeta."""
    texto = (_docs_sap() / "metadata.xml").read_text(encoding="utf-8")
    raiz = ET.fromstring(_AMP_SOLTO.sub("&amp;", texto))  # noqa: S314 — arquivo do proprio repo

    anotacoes: dict[str, set[str]] = {}
    for grupo in raiz.iter(f"{_EDM}Annotations"):
        alvo = grupo.get("Target", "").rsplit(".", 1)[-1]
        for a in grupo.findall(f"{_EDM}Annotation"):
            termo = a.get("Term", "").rsplit(".", 1)[-1]
            valor = (a.get("EnumMember") or "").rsplit("/", 1)[-1]
            anotacoes.setdefault(alvo, set()).add(f"{termo}={valor}" if valor else termo)

    resultado: dict[str, dict[str, PropriedadeMeta]] = {}
    for et in raiz.iter(f"{_EDM}EntityType"):
        nome_et = et.get("Name", "")
        props: dict[str, PropriedadeMeta] = {}
        for p in et.findall(f"{_EDM}Property"):
            nome = p.get("Name", "")
            props[nome] = PropriedadeMeta(
                nome=nome,
                tipo=p.get("Type", "").removeprefix("Edm."),
                max_length=int(p.get("MaxLength")) if p.get("MaxLength") else None,
                precision=int(p.get("Precision")) if p.get("Precision") else None,
                scale=p.get("Scale"),
                anotacoes=frozenset(anotacoes.get(f"{nome_et}/{nome}", set())),
            )
        resultado[nome_et] = props
    return resultado


def _converter(no: Any, chave: str | None = None) -> Any:
    if isinstance(no, dict):
        return {k: _converter(v, k) for k, v in no.items()}
    if isinstance(no, list):
        return [_converter(v, chave) for v in no]
    if chave in _DECIMAIS and isinstance(no, int | Decimal) and not isinstance(no, bool):
        return Decimal(no)
    if chave in _DATAS and isinstance(no, str):
        return date.fromisoformat(no)
    return no


def payload_exemplo_como_entrada() -> dict[str, Any]:
    bruto = json.loads(
        (_docs_sap() / "payload_exemplo.json").read_text(encoding="utf-8"), parse_float=Decimal
    )
    bruto.pop("StatusBlock")
    convertido: dict[str, Any] = _converter(bruto)
    return convertido
