"""Snapshot da submissao (D6') e conferencia das parcelas (D11). Tarefa 2.8.

- ``montar_contrato``: entrada do contrato SEM ``to_FormPag`` + ``EntradaParcelas``
  -> ``Contract`` validado, com as parcelas calculadas AQUI (``to_FormPag`` nunca
  vem do cliente). E o contrato congelado no snapshot.
- ``conferir``: antes do CSRF e do marcador, recalcula as parcelas com a MESMA
  versao do algoritmo e compara com o snapshot. Versao diferente: pula
  (``PULADA_VERSAO``: envia sem conferir, D11). O snapshot nunca e recalculado
  para envio: o worker manda exatamente o que foi congelado.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from app.application.ports import EntradaParcelas, SnapshotContrato
from app.domain.contract import Contract
from app.domain.installments import ALGORITMO_PARCELAS, ParcelaCalculada, calcular_parcelas
from app.domain.politica import POLITICA_PADRAO, PoliticaSalesOrg

Calculo = Callable[[Decimal, Sequence[int], Sequence[date]], tuple[ParcelaCalculada, ...]]


@dataclass(frozen=True, slots=True)
class Algoritmo:
    versao: str
    calcular: Calculo


ALGORITMO_ATUAL = Algoritmo(ALGORITMO_PARCELAS, calcular_parcelas)


class Conferencia(Enum):
    OK = "ok"
    PULADA_VERSAO = "pulada_versao"


@dataclass(frozen=True, slots=True)
class Divergencia:
    parcela: int  # 1..N
    campo: str  # nome OData do campo, ou "quantidade"

    def detalhe(self) -> dict[str, str | int]:
        return {"parcela": self.parcela, "campo": self.campo}


def montar_contrato(
    entrada: Mapping[str, object],
    parcelas: EntradaParcelas,
    *,
    algoritmo: Algoritmo = ALGORITMO_ATUAL,
    politica: Mapping[str, PoliticaSalesOrg] = POLITICA_PADRAO,
) -> Contract:
    if "to_FormPag" in entrada:
        raise ValueError("to_FormPag nunca vem do cliente: as parcelas sao calculadas no servidor")
    calculadas = algoritmo.calcular(parcelas.total, parcelas.pesos, parcelas.datas)
    moeda = entrada.get("TransactionCurrency")
    to_formpag = [
        {
            "Parcela": p.parcela,
            "TransactionCurrency": moeda,
            "Porcentagem": p.porcentagem,
            "Valor": p.valor,
            "Data": p.data,
            "FormPag": parcelas.form_pag,
        }
        for p in calculadas
    ]
    return Contract.criar({**entrada, "to_FormPag": to_formpag}, politica=politica)


def conferir(
    snapshot: SnapshotContrato, algoritmo: Algoritmo = ALGORITMO_ATUAL
) -> Conferencia | Divergencia:
    if snapshot.algoritmo_parcelas != algoritmo.versao:
        return Conferencia.PULADA_VERSAO
    e = snapshot.entrada_parcelas
    calculadas = algoritmo.calcular(e.total, e.pesos, e.datas)
    congeladas = snapshot.contrato.installments
    for n, (calc, cong) in enumerate(zip(calculadas, congeladas, strict=False), 1):
        for campo, esperado, atual in (
            ("Parcela", calc.parcela, cong.parcela),
            ("Porcentagem", calc.porcentagem, cong.porcentagem),
            ("Valor", calc.valor, cong.valor),
            ("Data", calc.data, cong.data),
            ("FormPag", e.form_pag, cong.form_pag),
        ):
            if esperado != atual:
                return Divergencia(parcela=n, campo=campo)
    if len(calculadas) != len(congeladas):
        return Divergencia(parcela=min(len(calculadas), len(congeladas)) + 1, campo="quantidade")
    return Conferencia.OK
