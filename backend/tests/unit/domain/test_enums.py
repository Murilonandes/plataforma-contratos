"""Testes de ``app.domain.enums``.

``enums.py`` so contem conceitos NOSSOS (estado do contrato, evento de
transicao, tipo de ator). Codigos do SAP (PartnerFunction, ConditionType,
FormPag, LongTextID, Language, SalesContractType...) sao ``Edm.String`` no
``$metadata`` e NAO viram enum: formato e validado no dominio e os valores
permitidos vem de config por sales org (ARCHITECTURE §8, decisao #11).

Os enums sao conferidos contra a matriz de transicoes do ARCHITECTURE §4:
mudar um sem o outro quebra este teste.
"""

from __future__ import annotations

import enum
import inspect

import pytest

from app.domain import enums
from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from tests.unit.domain._referencias_arquitetura import linhas_matriz, total_declarado

_ESTADOS = {
    "RASCUNHO",
    "NA_FILA",
    "ENVIANDO",
    "CRIADO",
    "ERRO_NEGOCIO",
    "ERRO_TECNICO",
    "INCERTO",
    "CANCELADO",
}
_EVENTOS = {
    "SUBMETER",
    "CANCELAR",
    "WORKER_PEGOU",
    "SAP_201",
    "SAP_4XX_NEGOCIO",
    "SAP_4XX_TECNICO",
    "FALHA_ANTES_POST",
    "FALHA_ANTES_POST_ESGOTOU",
    "TIMEOUT_APOS_POST",
    "CONEXAO_CAIDA_APOS_POST",
    "SAP_5XX_APOS_POST",
    "LOCK_EXPIRADO_SEM_ENVIO",
    "LOCK_EXPIRADO_COM_ENVIO",
    "LIBERAR_REENVIO",
    "RECONCILIAR_PARA_CRIADO",
    "CONFERENCIA_DIVERGENTE",
    "FALHA_APOS_RESPOSTA",
    "FALHA_NAO_CLASSIFICADA_ANTES_ENVIO",
    "FALHA_NAO_CLASSIFICADA_APOS_ENVIO",
}
_ATORES = {"user", "admin", "worker", "system"}


def _matriz() -> list[tuple[str, str, str, str]]:
    """(de, evento, para, ator) de cada linha da matriz do ARCHITECTURE §4."""
    return [(ln.de, ln.evento, ln.para, ln.ator) for ln in linhas_matriz()]


# ---- Conjuntos exatos --------------------------------------------------------


def test_contract_status_tem_exatamente_os_8_estados() -> None:
    assert {s.name for s in ContractStatus} == _ESTADOS
    assert len(ContractStatus) == 8


def test_transition_event_tem_exatamente_os_eventos_da_matriz() -> None:
    assert {e.name for e in TransitionEvent} == _EVENTOS
    assert len(TransitionEvent) == 19


def test_actor_kind_tem_os_4_atores() -> None:
    assert {a.value for a in ActorKind} == _ATORES
    assert len(ActorKind) == 4


# ---- Valores (StrEnum) -------------------------------------------------------


@pytest.mark.parametrize("enum_cls", [ContractStatus, TransitionEvent])
def test_valor_igual_ao_nome(enum_cls: type[enum.StrEnum]) -> None:
    for membro in enum_cls:
        assert membro.value == membro.name
        assert str(membro) == membro.name


def test_actor_kind_valores_em_minusculo_como_no_architecture() -> None:
    assert [a.value for a in ActorKind] == ["user", "admin", "worker", "system"]
    assert ActorKind.USER == "user"
    assert str(ActorKind.SYSTEM) == "system"


@pytest.mark.parametrize("enum_cls", [ContractStatus, TransitionEvent, ActorKind])
def test_sao_strenum(enum_cls: type[enum.Enum]) -> None:
    assert issubclass(enum_cls, enum.StrEnum)


def test_construcao_por_valor_e_rejeicao_de_valor_desconhecido() -> None:
    assert ContractStatus("INCERTO") is ContractStatus.INCERTO
    assert ActorKind("worker") is ActorKind.WORKER
    with pytest.raises(ValueError, match=r"^'SAP_OK' is not a valid TransitionEvent$"):
        TransitionEvent("SAP_OK")


# ---- Sincronia com o ARCHITECTURE §4 -----------------------------------------


def test_matriz_do_architecture_tem_25_transicoes() -> None:
    assert len(_matriz()) == 25
    assert total_declarado() == len(_matriz())  # a linha 'Total' acompanha a tabela


def test_estados_da_matriz_sao_exatamente_os_do_enum() -> None:
    estados = {de for de, _, _, _ in _matriz()} | {para for _, _, para, _ in _matriz()}
    assert estados == {s.value for s in ContractStatus}


def test_eventos_da_matriz_sao_exatamente_os_do_enum() -> None:
    assert {ev for _, ev, _, _ in _matriz()} == {e.value for e in TransitionEvent}


def test_atores_da_matriz_sao_exatamente_os_do_enum() -> None:
    assert {ator for _, _, _, ator in _matriz()} == {a.value for a in ActorKind}


# ---- Escopo: so conceitos nossos ---------------------------------------------


def test_modulo_so_define_os_tres_enums_nossos() -> None:
    definidos = {
        nome
        for nome, obj in inspect.getmembers(enums, inspect.isclass)
        if issubclass(obj, enum.Enum) and obj.__module__ == enums.__name__
    }
    assert definidos == {"ContractStatus", "TransitionEvent", "ActorKind"}


@pytest.mark.parametrize(
    "codigo_sap",
    [
        "PartnerFunction",
        "ConditionType",
        "FormPag",
        "LongTextID",
        "Language",
        "SalesContractType",
        "StatusBlock",
    ],
)
def test_codigos_do_sap_nao_viram_enum(codigo_sap: str) -> None:
    assert not hasattr(enums, codigo_sap)
