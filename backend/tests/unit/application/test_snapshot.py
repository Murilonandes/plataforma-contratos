"""Snapshot da submissao (D6') e conferencia das parcelas (D11). Tarefa 2.8."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.application.ports import EntradaParcelas, SnapshotContrato
from app.application.snapshot import (
    ALGORITMO_ATUAL,
    Algoritmo,
    Conferencia,
    Divergencia,
    conferir,
    montar_contrato,
)
from app.domain.errors import DomainValidationError
from app.domain.installments import ALGORITMO_PARCELAS, ParcelaCalculada, calcular_parcelas
from tests.unit.domain._referencias_sap import payload_exemplo_como_entrada

PARCELAS = EntradaParcelas(
    total=Decimal("23299.55"),
    pesos=(1, 1, 1),
    datas=(date(2026, 9, 4), date(2026, 10, 4), date(2026, 11, 3)),
    form_pag="K",
)


def entrada_sem_parcelas() -> dict[str, Any]:
    entrada = payload_exemplo_como_entrada()
    entrada.pop("to_FormPag")
    return entrada


def _snapshot(algoritmo: str = ALGORITMO_PARCELAS) -> SnapshotContrato:
    contrato = montar_contrato(entrada_sem_parcelas(), PARCELAS)
    return SnapshotContrato(
        id=uuid4(),
        contract_id=uuid4(),
        contrato=contrato,
        algoritmo_parcelas=algoritmo,
        entrada_parcelas=PARCELAS,
    )


def test_versao_do_algoritmo_atual() -> None:
    assert ALGORITMO_PARCELAS == "maior-resto/1"
    assert Algoritmo(ALGORITMO_PARCELAS, calcular_parcelas) == ALGORITMO_ATUAL


def test_montar_contrato_calcula_as_parcelas_no_servidor() -> None:
    contrato = montar_contrato(entrada_sem_parcelas(), PARCELAS)
    assert [
        (p.parcela, p.porcentagem, p.valor, p.data, p.form_pag, p.transaction_currency)
        for p in contrato.installments
    ] == [
        (1, Decimal("33.3334"), Decimal("7766.52"), date(2026, 9, 4), "K", "BRL"),
        (2, Decimal("33.3333"), Decimal("7766.52"), date(2026, 10, 4), "K", "BRL"),
        (3, Decimal("33.3333"), Decimal("7766.51"), date(2026, 11, 3), "K", "BRL"),
    ]
    # igual ao payload de referencia (que traz as parcelas prontas)
    assert contrato.installments == montar_referencia().installments


def montar_referencia() -> Any:
    from app.domain.contract import Contract

    return Contract.criar(payload_exemplo_como_entrada())


def test_to_formpag_nunca_vem_do_cliente() -> None:
    entrada = payload_exemplo_como_entrada()  # ainda com to_FormPag
    with pytest.raises(ValueError) as exc:  # noqa: PT011 — mensagem conferida abaixo
        montar_contrato(entrada, PARCELAS)
    assert (
        str(exc.value) == "to_FormPag nunca vem do cliente: as parcelas sao calculadas no servidor"
    )


def test_entrada_de_parcelas_invalida_levanta_erro_de_dominio() -> None:
    ruim = dataclasses.replace(PARCELAS, pesos=(1, 1))
    with pytest.raises(DomainValidationError) as exc:
        montar_contrato(entrada_sem_parcelas(), ruim)
    assert [e.path for e in exc.value.errors] == ["datas"]


# ---- Conferencia ---------------------------------------------------------------------------


def test_conferencia_ok() -> None:
    assert conferir(_snapshot()) is Conferencia.OK


def test_versao_diferente_pula_a_conferencia_sem_calcular() -> None:
    def explode(*_: object) -> tuple[ParcelaCalculada, ...]:
        raise AssertionError("nao deveria calcular")

    assert conferir(_snapshot("maior-resto/0"), Algoritmo(ALGORITMO_PARCELAS, explode)) is (
        Conferencia.PULADA_VERSAO
    )


def _algoritmo_com(
    mudar: Any,
) -> Algoritmo:
    def calcular(
        total: Decimal, pesos: Sequence[int], datas: Sequence[date]
    ) -> tuple[ParcelaCalculada, ...]:
        resultado: tuple[ParcelaCalculada, ...] = mudar(calcular_parcelas(total, pesos, datas))
        return resultado

    return Algoritmo(ALGORITMO_PARCELAS, calcular)


@pytest.mark.parametrize(
    ("mudar", "esperado"),
    [
        (
            lambda ps: (ps[0], dataclasses.replace(ps[1], valor=Decimal("7766.53")), ps[2]),
            Divergencia(parcela=2, campo="Valor"),
        ),
        (
            lambda ps: (ps[0], ps[1], dataclasses.replace(ps[2], porcentagem=Decimal("33.3334"))),
            Divergencia(parcela=3, campo="Porcentagem"),
        ),
        (
            lambda ps: (dataclasses.replace(ps[0], data=date(2026, 9, 5)), ps[1], ps[2]),
            Divergencia(parcela=1, campo="Data"),
        ),
        (
            lambda ps: (dataclasses.replace(ps[0], parcela=9), ps[1], ps[2]),
            Divergencia(parcela=1, campo="Parcela"),
        ),
        (lambda ps: ps[:2], Divergencia(parcela=3, campo="quantidade")),
        (
            lambda ps: (*ps, dataclasses.replace(ps[2], parcela=4)),
            Divergencia(parcela=4, campo="quantidade"),
        ),
    ],
)
def test_divergencia_aponta_a_primeira_parcela_e_o_campo(mudar: Any, esperado: Divergencia) -> None:
    assert conferir(_snapshot(), _algoritmo_com(mudar)) == esperado


def test_form_pag_diferente_no_snapshot_diverge() -> None:
    s = _snapshot()
    trocada = dataclasses.replace(s.contrato.installments[1], form_pag="B")
    contrato = dataclasses.replace(
        s.contrato, installments=(s.contrato.installments[0], trocada, s.contrato.installments[2])
    )
    assert conferir(dataclasses.replace(s, contrato=contrato)) == Divergencia(
        parcela=2, campo="FormPag"
    )


def test_divergencia_vira_detalhe_do_evento() -> None:
    assert Divergencia(parcela=2, campo="Valor").detalhe() == {"parcela": 2, "campo": "Valor"}
