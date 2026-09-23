"""Testes de ``app.domain.errors``.

Contrato de erros do dominio (consumido pelo front via API):
- ``FieldError``: ``path`` no formato do payload OData (``to_Item[1].Material``,
  indice base 0), ``code`` estavel em snake_case, ``message`` em PT-BR e
  ``params`` (JSON-safe). O front mapeia por ``code`` + ``path``, nunca pelo texto.
- ``ErrorCode``: lista unica de codes, sem duplicata.
- ``ErrorCollector``/``DomainValidationError``: a validacao ACUMULA todos os
  erros e levanta uma vez, no fim.
- ``InvalidTransitionError``: separado, com ``from_status`` e ``event``.

Mensagens conferidas por igualdade exata (CLAUDE.md, secao Qualidade).
"""

from __future__ import annotations

import enum
import re
import string

import pytest

from app.domain.enums import ContractStatus, TransitionEvent
from app.domain.errors import (
    MENSAGENS,
    DomainError,
    DomainValidationError,
    ErrorCode,
    ErrorCollector,
    FieldError,
    InvalidTransitionError,
    campo,
    indice,
)

# ---- ErrorCode: lista unica, estavel, snake_case -----------------------------


def test_error_code_sem_valor_duplicado() -> None:
    # __members__ inclui aliases: valor repetido apareceria aqui como membro extra
    assert len({c.value for c in ErrorCode}) == len(ErrorCode.__members__)


def test_error_code_e_unique() -> None:
    with pytest.raises(ValueError, match=r"^duplicate values found in <enum 'Dup'>: B -> A$"):

        @enum.unique
        class Dup(enum.StrEnum):
            A = "x"
            B = "x"

    # o proprio ErrorCode passa pelo @unique sem erro
    assert enum.unique(ErrorCode) is ErrorCode


@pytest.mark.parametrize("code", list(ErrorCode))
def test_error_code_em_snake_case_e_valor_igual_ao_nome_minusculo(code: ErrorCode) -> None:
    assert re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)*", code.value)
    assert code.value == code.name.lower()


def test_codes_conhecidos_estao_presentes() -> None:
    assert {"required", "max_length", "decimal_scale", "duplicate_partner_function"} <= {
        c.value for c in ErrorCode
    }


# ---- Mensagens: uma por code, placeholders coerentes -------------------------


def test_todo_code_tem_mensagem_e_nenhuma_mensagem_sobra() -> None:
    assert set(MENSAGENS) == set(ErrorCode)


@pytest.mark.parametrize("code", list(ErrorCode))
def test_mensagem_so_usa_campo_e_params_declarados(code: ErrorCode) -> None:
    template, params = MENSAGENS[code]
    usados = {nome for _, nome, _, _ in string.Formatter().parse(template) if nome}
    assert usados <= {"campo", *params}
    assert set(params) <= usados, f"param declarado e nao usado na mensagem de {code}"


# ---- FieldError --------------------------------------------------------------


def test_field_error_monta_mensagem_em_pt_br_a_partir_do_code() -> None:
    erro = FieldError.criar("to_Item[0].SalesContractItemText", ErrorCode.MAX_LENGTH, max=40)
    assert erro.path == "to_Item[0].SalesContractItemText"
    assert erro.code is ErrorCode.MAX_LENGTH
    assert erro.message == "campo 'SalesContractItemText' excede 40 caracteres"
    assert dict(erro.params) == {"max": 40}


@pytest.mark.parametrize(
    ("path", "code", "params", "mensagem"),
    [
        ("SalesContractType", ErrorCode.REQUIRED, {}, "campo 'SalesContractType' e obrigatorio"),
        (
            "to_Item[2].RequestedQuantity",
            ErrorCode.DECIMAL_SCALE,
            {"max": 3},
            "campo 'RequestedQuantity' aceita no maximo 3 casas decimais",
        ),
        (
            "to_Partner[1].PartnerFunction",
            ErrorCode.DUPLICATE_PARTNER_FUNCTION,
            {"funcao": "Y1"},
            "parceiro duplicado para funcao 'Y1'",
        ),
    ],
)
def test_mensagens_exatas(
    path: str, code: ErrorCode, params: dict[str, object], mensagem: str
) -> None:
    assert FieldError.criar(path, code, **params).message == mensagem  # type: ignore[arg-type]


def test_field_error_exige_exatamente_os_params_do_code() -> None:
    with pytest.raises(
        ValueError, match=r"^params de 'max_length' deveriam ser \['max'\], vieram \[\]$"
    ):
        FieldError.criar("X", ErrorCode.MAX_LENGTH)
    with pytest.raises(
        ValueError, match=r"^params de 'required' deveriam ser \[\], vieram \['extra'\]$"
    ):
        FieldError.criar("X", ErrorCode.REQUIRED, extra=1)


def test_field_error_params_sao_imutaveis_e_json_safe() -> None:
    erro = FieldError.criar("A.B", ErrorCode.MAX_LENGTH, max=4)
    with pytest.raises(TypeError):
        erro.params["max"] = 5  # type: ignore[index]
    with pytest.raises(TypeError, match=r"^param 'max' precisa ser str, int, bool ou None$"):
        FieldError.criar("A.B", ErrorCode.MAX_LENGTH, max=4.0)  # type: ignore[arg-type]


def test_field_error_e_imutavel() -> None:
    erro = FieldError.criar("A", ErrorCode.REQUIRED)
    with pytest.raises(AttributeError):
        erro.path = "B"  # type: ignore[misc]


def test_com_prefixo_reescreve_path_e_mensagem_continua_igual() -> None:
    erro = FieldError.criar("Material", ErrorCode.REQUIRED)
    prefixado = erro.com_prefixo(indice("to_Item", 1))
    assert prefixado.path == "to_Item[1].Material"
    assert prefixado.message == "campo 'Material' e obrigatorio"
    assert erro.path == "Material"


# ---- Helpers de path ---------------------------------------------------------


def test_helpers_de_path() -> None:
    assert campo("", "SalesContractType") == "SalesContractType"
    assert campo("to_Item[1]", "Material") == "to_Item[1].Material"
    assert indice("to_Item", 0) == "to_Item[0]"
    assert indice(campo("to_Item[1]", "to_PricingElement"), 2) == (
        "to_Item[1].to_PricingElement[2]"
    )


def test_indice_rejeita_negativo() -> None:
    with pytest.raises(ValueError, match=r"^indice precisa ser >= 0, veio -1$"):
        indice("to_Item", -1)


# ---- Acumulacao: todos os erros, levantados uma vez --------------------------


def test_coletor_acumula_erros_de_lugares_diferentes_e_levanta_uma_vez() -> None:
    coletor = ErrorCollector()
    coletor.adicionar("SalesContractType", ErrorCode.REQUIRED)
    coletor.adicionar(campo(indice("to_Item", 1), "Material"), ErrorCode.REQUIRED)
    coletor.adicionar(campo(indice("to_FormPag", 2), "Porcentagem"), ErrorCode.DECIMAL_SCALE, max=4)
    with pytest.raises(DomainValidationError) as exc:
        coletor.levantar_se_houver()
    assert [(e.path, e.code) for e in exc.value.errors] == [
        ("SalesContractType", ErrorCode.REQUIRED),
        ("to_Item[1].Material", ErrorCode.REQUIRED),
        ("to_FormPag[2].Porcentagem", ErrorCode.DECIMAL_SCALE),
    ]
    assert str(exc.value) == (
        "3 erro(s) de validacao: "
        "SalesContractType: campo 'SalesContractType' e obrigatorio; "
        "to_Item[1].Material: campo 'Material' e obrigatorio; "
        "to_FormPag[2].Porcentagem: campo 'Porcentagem' aceita no maximo 4 casas decimais"
    )


def test_coletor_sem_erros_nao_levanta() -> None:
    coletor = ErrorCollector()
    coletor.levantar_se_houver()
    assert coletor.erros == ()
    assert not coletor


def test_coletor_incorpora_erros_de_filho_com_prefixo() -> None:
    filho = ErrorCollector()
    filho.adicionar("Material", ErrorCode.REQUIRED)
    filho.adicionar("SalesContractItemText", ErrorCode.MAX_LENGTH, max=40)

    pai = ErrorCollector()
    pai.adicionar("SoldToParty", ErrorCode.REQUIRED)
    pai.incorporar(filho.erros, prefixo=indice("to_Item", 0))
    assert bool(pai)
    assert [e.path for e in pai.erros] == [
        "SoldToParty",
        "to_Item[0].Material",
        "to_Item[0].SalesContractItemText",
    ]


def test_coletor_incorpora_domain_validation_error_de_um_filho() -> None:
    filho = ErrorCollector()
    filho.adicionar("Parcela", ErrorCode.REQUIRED)
    pai = ErrorCollector()
    try:
        filho.levantar_se_houver()
    except DomainValidationError as exc:
        pai.incorporar(exc.errors, prefixo=indice("to_FormPag", 3))
    assert [e.path for e in pai.erros] == ["to_FormPag[3].Parcela"]


def test_domain_validation_error_exige_ao_menos_um_erro() -> None:
    with pytest.raises(ValueError, match=r"^DomainValidationError precisa de ao menos um erro$"):
        DomainValidationError(())


def test_hierarquia() -> None:
    assert issubclass(DomainValidationError, DomainError)
    assert issubclass(InvalidTransitionError, DomainError)
    assert not issubclass(InvalidTransitionError, DomainValidationError)


# ---- InvalidTransitionError --------------------------------------------------


def test_invalid_transition_error_carrega_estado_evento_e_mensagem() -> None:
    erro = InvalidTransitionError(ContractStatus.CRIADO, TransitionEvent.SUBMETER)
    assert erro.from_status is ContractStatus.CRIADO
    assert erro.event is TransitionEvent.SUBMETER
    assert str(erro) == "transicao invalida: evento 'SUBMETER' nao e permitido no estado 'CRIADO'"


# ---- Mensagens nao citam indice base 0 ----------------------------------------

_PARAMS_EXEMPLO: dict[ErrorCode, dict[str, object]] = {
    ErrorCode.MAX_LENGTH: {"max": 40},
    ErrorCode.DECIMAL_SCALE: {"max": 3},
    ErrorCode.DECIMAL_PRECISION: {"max": 12},
    ErrorCode.MIN_ITEMS: {"min": 1},
    ErrorCode.LENGTH_MISMATCH: {"esperado": 3, "recebido": 2},
    ErrorCode.DUPLICATE_PARTNER_FUNCTION: {"funcao": "Y1"},
    ErrorCode.INVALID_TYPE: {"tipo": "texto"},
}


@pytest.mark.parametrize("code", list(ErrorCode))
@pytest.mark.parametrize(
    "path",
    [
        "to_Item[0].Material",
        "to_Item[1]",
        "to_FormPag[2].Porcentagem",
        "to_FormPag[0]",
        "to_Item[3].to_PricingElement[1].ConditionType",
        "to_Partner[4]",
    ],
)
def test_mensagem_nao_cita_indice_base_0(code: ErrorCode, path: str) -> None:
    erro = FieldError.criar(path, code, **_PARAMS_EXEMPLO.get(code, {}))  # type: ignore[arg-type]
    assert re.search(r"\[\d+\]", erro.message) is None, erro.message
    assert erro.path == path  # a posicao fica no path, para o front


def test_erro_no_elemento_inteiro_usa_o_nome_da_lista_sem_indice() -> None:
    erro = FieldError.criar("to_Partner[1]", ErrorCode.PARTNER_IDENTIFIER_REQUIRED)
    assert erro.message == "campo 'to_Partner' precisa de ao menos um identificador de parceiro"
