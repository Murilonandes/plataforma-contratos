"""As especificacoes de campo do dominio espelham o ``$metadata`` do servico.

Fonte da verdade: ``docs/sap/metadata.xml``. Se o SAP mudar um MaxLength, um
Mandatory ou uma escala, este teste quebra ate o dominio acompanhar.
"""

from __future__ import annotations

import pytest

from app.domain.contract import ESPECIFICACOES, Campo, Tipo
from tests.unit.domain._referencias_sap import metadata

# Campos que existem no metadata mas NAO entram na entrada do dominio.
_FORA_DO_DOMINIO = {
    "StatusBlock": "gravado pelo mapper, sempre '06' (CLAUDE.md)",
}

_TIPO_EDM = {
    Tipo.TEXTO: "String",
    Tipo.DECIMAL: "Decimal",
    Tipo.DATA: "Date",
    Tipo.INTEIRO: "Int32",
}

# Strings sem MaxLength no metadata e com limite provisorio no dominio (decisao #5).
_MAX_PROVISORIO = {("TextosContratoType", "LongText"): 1000}


def _casos() -> list[tuple[str, Campo]]:
    return [(entidade, c) for entidade, campos in ESPECIFICACOES.items() for c in campos]


def test_especificacoes_cobrem_as_entidades_de_criacao() -> None:
    assert set(ESPECIFICACOES) == {
        "CriaContratoType",
        "ItensContratoType",
        "PrecosItemType",
        "PrecosCabecalhoType",
        "ParceirosContratoType",
        "ParcelasContratoType",
        "TextosContratoType",
    }


@pytest.mark.parametrize("entidade", sorted(ESPECIFICACOES))
def test_todo_campo_editavel_do_metadata_esta_no_dominio_e_vice_versa(entidade: str) -> None:
    meta = metadata()[entidade]
    editaveis = {n for n, p in meta.items() if not p.computed and n not in _FORA_DO_DOMINIO}
    assert {c.odata for c in ESPECIFICACOES[entidade]} == editaveis


@pytest.mark.parametrize(("entidade", "c"), _casos(), ids=lambda x: getattr(x, "odata", x))
def test_campo_espelha_o_metadata(entidade: str, c: Campo) -> None:
    p = metadata()[entidade][c.odata]
    assert not p.computed, "campo Computed nunca e entrada"
    assert _TIPO_EDM[c.tipo] == p.tipo
    assert c.obrigatorio == p.mandatory, "obrigatoriedade = FieldControl/Mandatory, nada mais"
    assert c.maiusculo == p.upper, "uppercase so em campo IsUpperCase"
    if c.tipo is Tipo.TEXTO:
        esperado = _MAX_PROVISORIO.get((entidade, c.odata), p.max_length)
        assert c.max_len == esperado
    else:
        assert c.max_len is None
    if c.tipo is Tipo.DECIMAL:
        assert p.precision is not None
        assert c.precisao == p.precision
        escala_meta = 2 if p.scale == "variable" else int(p.scale or "0")  # BRL: 2 casas
        assert c.escala == escala_meta
    else:
        assert c.escala is None
        assert c.precisao is None


def test_uppercase_so_no_form_pag() -> None:
    """Das 9 anotacoes IsUpperCase, 8 sao do SalesContract (Computed); sobra FormPag."""
    maiusculos = [(e, c.odata) for e, c in _casos() if c.maiusculo]
    assert maiusculos == [("ParcelasContratoType", "FormPag")]


def test_status_block_e_computed_nunca_sao_entrada() -> None:
    nomes = {c.odata for _, c in _casos()}
    assert not nomes & {"StatusBlock", "SalesContract", "SalesContractItem", "ConditionUUID"}
