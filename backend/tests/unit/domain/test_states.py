"""Maquina de estados (``states.py``).

Fonte unica: a matriz do ARCHITECTURE §4. O teste le as 25 linhas e confere a
``MATRIZ`` do codigo campo a campo (de, evento, para, ator, justificativa) e o
conjunto que exige ``sap_contract_number`` (linhas cuja Observacao o cita).
Exaustivo 8 estados x 19 eventos: fora da matriz -> ``InvalidTransitionError``.

Dados por transicao (erros acumulados numa ``DomainValidationError``):
- ``ator.kind`` = coluna Ator; ``ator.identifier`` nao vazio.
- justificativa: "sim" -> obrigatoria; "nao" + user/admin -> opcional; "nao" +
  worker/system -> proibida. Quando vem: 10 a 500 caracteres depois do strip.
- ``sap_contract_number``: obrigatorio em ``SAP_201`` e ``RECONCILIAR_PARA_CRIADO``
  (ate 10, so digitos ASCII, ``TODO(decisao #13)``); proibido nas demais.
- ``detalhe``: so worker/system; chaves snake_case, valores ``int`` ou ``str``.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Mapping
from typing import Any

import pytest

from app.domain.enums import ActorKind, ContractStatus, TransitionEvent
from app.domain.errors import DomainValidationError, ErrorCode, InvalidTransitionError
from app.domain.states import MATRIZ, Ator, Regra, Transicao, transition
from tests.unit.domain._referencias_arquitetura import LinhaMatriz, linhas_matriz

S = ContractStatus
E = TransitionEvent
K = ActorKind

_ATORES = {k: Ator(k, f"id-{k.value}") for k in ActorKind}
_JUSTIFICATIVA = "Conferido na VA43 com o comercial."
_NUMERO_SAP = "4000012345"


def _regra(de: S, evento: E) -> Regra:
    return MATRIZ[(de, evento)]


def _validos(de: S, evento: E) -> dict[str, Any]:
    """Argumentos minimos validos para a transicao."""
    r = _regra(de, evento)
    kwargs: dict[str, Any] = {"ator": _ATORES[r.ator]}
    if r.exige_justificativa:
        kwargs["justificativa"] = _JUSTIFICATIVA
    if r.exige_numero_sap:
        kwargs["sap_contract_number"] = _NUMERO_SAP
    return kwargs


def _erros(de: S, evento: E, **kwargs: Any) -> list[tuple[str, ErrorCode, dict[str, Any]]]:
    with pytest.raises(DomainValidationError) as exc:
        transition(de, evento, **kwargs)
    return [(e.path, e.code, dict(e.params)) for e in exc.value.errors]


def _com(de: S, evento: E, **extra: Any) -> dict[str, Any]:
    return {**_validos(de, evento), **extra}


_VALIDAS = sorted(MATRIZ, key=lambda p: (p[0].value, p[1].value))
_INVALIDAS = [p for p in itertools.product(ContractStatus, TransitionEvent) if p not in MATRIZ]


def _ids(valor: S | E) -> str:
    """Id por valor (o pytest junta de-evento com '-')."""
    return valor.value


def _linhas_por(pred: Any) -> list[tuple[S, E]]:
    return [p for p in _VALIDAS if pred(MATRIZ[p])]


_COM_JUSTIFICATIVA = _linhas_por(lambda r: r.exige_justificativa)
_JUSTIFICATIVA_OPCIONAL = _linhas_por(
    lambda r: not r.exige_justificativa and r.ator in {K.USER, K.ADMIN}
)
_MAQUINA = _linhas_por(lambda r: r.ator in {K.WORKER, K.SYSTEM})
_HUMANO = _linhas_por(lambda r: r.ator in {K.USER, K.ADMIN})
_COM_NUMERO = _linhas_por(lambda r: r.exige_numero_sap)
_SEM_NUMERO = _linhas_por(lambda r: not r.exige_numero_sap)


# ---- Sincronia com o ARCHITECTURE §4 (fonte unica) ---------------------------


def test_matriz_do_codigo_e_a_tabela_do_architecture_campo_a_campo() -> None:
    doc = {(ln.de, ln.evento): (ln.para, ln.ator, ln.justificativa) for ln in linhas_matriz()}
    codigo = {
        (de.value, ev.value): (r.para.value, r.ator.value, r.exige_justificativa)
        for (de, ev), r in MATRIZ.items()
    }
    assert len(linhas_matriz()) == 25
    assert codigo == doc


def test_numero_sap_exigido_exatamente_onde_a_observacao_cita() -> None:
    def cita(ln: LinhaMatriz) -> bool:
        return "`sap_contract_number`" in ln.observacao

    doc = {(ln.de, ln.evento) for ln in linhas_matriz() if cita(ln)}
    codigo = {(de.value, ev.value) for (de, ev), r in MATRIZ.items() if r.exige_numero_sap}
    assert codigo == doc == {("ENVIANDO", "SAP_201"), ("INCERTO", "RECONCILIAR_PARA_CRIADO")}


def test_matriz_e_imutavel() -> None:
    with pytest.raises(TypeError):
        MATRIZ[(S.CRIADO, E.SUBMETER)] = MATRIZ[(S.RASCUNHO, E.SUBMETER)]  # type: ignore[index]


# ---- Exaustivo: 8 estados x 19 eventos ---------------------------------------


def test_exaustivo_tem_152_pares_25_validos() -> None:
    assert len(_VALIDAS) + len(_INVALIDAS) == 8 * 19
    assert len(_VALIDAS) == 25


@pytest.mark.parametrize(("de", "evento"), _VALIDAS, ids=_ids)
def test_transicao_valida(de: S, evento: E) -> None:
    kwargs = _validos(de, evento)
    t = transition(de, evento, **kwargs)
    r = _regra(de, evento)
    assert t == Transicao(
        de=de,
        para=r.para,
        evento=evento,
        ator=kwargs["ator"],
        justificativa=kwargs.get("justificativa"),
        sap_contract_number=kwargs.get("sap_contract_number"),
        detalhe={},
    )


@pytest.mark.parametrize(("de", "evento"), _INVALIDAS, ids=_ids)
def test_par_fora_da_matriz(de: S, evento: E) -> None:
    for ator in _ATORES.values():
        with pytest.raises(InvalidTransitionError) as exc:
            transition(de, evento, ator=ator, justificativa=_JUSTIFICATIVA, sap_contract_number="1")
        assert str(exc.value) == (
            f"transicao invalida: evento '{evento.value}' nao e permitido no estado '{de.value}'"
        )
        assert exc.value.from_status is de
        assert exc.value.event is evento


def test_par_invalido_vence_dados_invalidos() -> None:
    """Fora da matriz nem olha os dados: InvalidTransitionError, nunca validacao."""
    with pytest.raises(InvalidTransitionError):
        transition(S.CRIADO, E.CANCELAR, ator=Ator(K.USER, " "), justificativa="x")


@pytest.mark.parametrize("terminal", [S.CRIADO, S.CANCELADO])
def test_terminais_nao_tem_saida(terminal: S) -> None:
    assert not [p for p in MATRIZ if p[0] is terminal]


def test_so_falha_antes_do_post_ou_lock_sem_envio_voltam_para_a_fila() -> None:
    """O que decide NAO reenviar: de ENVIANDO, so estes dois voltam a NA_FILA."""
    volta = {ev for (de, ev), r in MATRIZ.items() if de is S.ENVIANDO and r.para is S.NA_FILA}
    assert volta == {E.FALHA_ANTES_POST, E.LOCK_EXPIRADO_SEM_ENVIO}


@pytest.mark.parametrize(
    "evento",
    [
        E.TIMEOUT_APOS_POST,
        E.CONEXAO_CAIDA_APOS_POST,
        E.SAP_5XX_APOS_POST,
        E.LOCK_EXPIRADO_COM_ENVIO,
    ],
)
def test_post_que_pode_ter_chegado_vai_para_incerto(evento: E) -> None:
    assert transition(S.ENVIANDO, evento, **_validos(S.ENVIANDO, evento)).para is S.INCERTO


@pytest.mark.parametrize(
    ("evento", "para"),
    [
        (E.CONFERENCIA_DIVERGENTE, S.ERRO_TECNICO),  # nada enviado: snapshot diverge
        (E.FALHA_NAO_CLASSIFICADA_ANTES_ENVIO, S.ERRO_TECNICO),  # sem marcador commitado
        (E.FALHA_APOS_RESPOSTA, S.INCERTO),  # SAP respondeu, nos falhamos
        (E.FALHA_NAO_CLASSIFICADA_APOS_ENVIO, S.INCERTO),  # com marcador
    ],
)
def test_eventos_de_falha_do_worker(evento: E, para: S) -> None:
    r = _regra(S.ENVIANDO, evento)
    assert (r.para, r.ator, r.exige_justificativa, r.exige_numero_sap) == (
        para,
        K.WORKER,
        False,
        False,
    )


def test_saida_de_incerto_e_so_de_admin_com_justificativa() -> None:
    saidas = {ev: r for (de, ev), r in MATRIZ.items() if de is S.INCERTO}
    assert set(saidas) == {E.RECONCILIAR_PARA_CRIADO, E.LIBERAR_REENVIO, E.CANCELAR}
    assert all(r.ator is K.ADMIN and r.exige_justificativa for r in saidas.values())


# ---- Ator --------------------------------------------------------------------


@pytest.mark.parametrize(("de", "evento"), _VALIDAS, ids=_ids)
def test_ator_de_outro_kind_e_rejeitado(de: S, evento: E) -> None:
    esperado = _regra(de, evento).ator
    for kind in ActorKind:
        if kind is esperado:
            continue
        assert _erros(de, evento, **_com(de, evento, ator=_ATORES[kind])) == [
            (
                "ator.kind",
                ErrorCode.ACTOR_NOT_ALLOWED,
                {"esperado": esperado.value, "recebido": kind.value},
            )
        ]


@pytest.mark.parametrize("identifier", ["", "   ", "\t\n"])
def test_identifier_vazio(identifier: str) -> None:
    ator = Ator(K.USER, identifier)
    assert _erros(S.RASCUNHO, E.SUBMETER, ator=ator) == [
        ("ator.identifier", ErrorCode.REQUIRED, {})
    ]


def test_identifier_e_gravado_sem_espacos() -> None:
    t = transition(S.RASCUNHO, E.SUBMETER, ator=Ator(K.USER, "  oid-123  "))
    assert t.ator == Ator(K.USER, "oid-123")


@pytest.mark.parametrize(
    ("kind", "identifier"),
    [("user", "oid"), (K.USER, None), (K.USER, 123), (None, "oid")],
)
def test_ator_confere_tipos(kind: object, identifier: object) -> None:
    with pytest.raises(TypeError, match=r"^Ator: tipo invalido \((kind|identifier)\)$"):
        Ator(kind, identifier)  # type: ignore[arg-type]


def test_transition_exige_ator_do_tipo_ator() -> None:
    with pytest.raises(TypeError, match=r"^transition: ator precisa ser Ator$"):
        transition(S.RASCUNHO, E.SUBMETER, ator=("user", "oid"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("atual", "evento", "msg"),
    [
        ("RASCUNHO", E.SUBMETER, "atual precisa ser ContractStatus"),
        (S.RASCUNHO, "SUBMETER", "evento precisa ser TransitionEvent"),
    ],
)
def test_estado_e_evento_precisam_ser_os_enums(atual: object, evento: object, msg: str) -> None:
    """StrEnum tem o hash da string: sem o check, 'RASCUNHO' acharia a regra no dict."""
    with pytest.raises(TypeError, match=rf"^transition: {msg}$"):
        transition(atual, evento, ator=_ATORES[K.USER])  # type: ignore[arg-type]


# ---- Justificativa -----------------------------------------------------------


def test_linhas_de_cada_regime_de_justificativa() -> None:
    assert len(_COM_JUSTIFICATIVA) == 7
    assert set(_JUSTIFICATIVA_OPCIONAL) == {
        (S.RASCUNHO, E.SUBMETER),
        (S.RASCUNHO, E.CANCELAR),
        (S.ERRO_NEGOCIO, E.SUBMETER),
    }
    assert len(_MAQUINA) == 15
    assert len(_HUMANO) == 10


@pytest.mark.parametrize(("de", "evento"), _COM_JUSTIFICATIVA, ids=_ids)
@pytest.mark.parametrize("ausente", [None, "", "          "])
def test_justificativa_obrigatoria(de: S, evento: E, ausente: str | None) -> None:
    assert _erros(de, evento, **_com(de, evento, justificativa=ausente)) == [
        ("justificativa", ErrorCode.REQUIRED, {})
    ]


@pytest.mark.parametrize(("de", "evento"), _COM_JUSTIFICATIVA + _JUSTIFICATIVA_OPCIONAL, ids=_ids)
@pytest.mark.parametrize(
    ("texto", "erro"),
    [
        (".", (ErrorCode.MIN_LENGTH, {"min": 10})),
        ("ok", (ErrorCode.MIN_LENGTH, {"min": 10})),
        ("  123456789  ", (ErrorCode.MIN_LENGTH, {"min": 10})),
        ("x" * 501, (ErrorCode.MAX_LENGTH, {"max": 500})),
        (123, (ErrorCode.INVALID_TYPE, {"tipo": "texto"})),
    ],
)
def test_justificativa_invalida_quando_vem(
    de: S, evento: E, texto: object, erro: tuple[ErrorCode, dict[str, Any]]
) -> None:
    assert _erros(de, evento, **_com(de, evento, justificativa=texto)) == [("justificativa", *erro)]


@pytest.mark.parametrize(("de", "evento"), _COM_JUSTIFICATIVA + _JUSTIFICATIVA_OPCIONAL, ids=_ids)
@pytest.mark.parametrize(
    ("texto", "gravado"),
    [
        ("1234567890", "1234567890"),
        ("  1234567890  ", "1234567890"),
        ("x" * 500, "x" * 500),
        (" " + "x" * 500 + " ", "x" * 500),
    ],
)
def test_justificativa_nos_limites_e_gravada_com_strip(
    de: S, evento: E, texto: str, gravado: str
) -> None:
    t = transition(de, evento, **_com(de, evento, justificativa=texto))
    assert t.justificativa == gravado


@pytest.mark.parametrize(("de", "evento"), _JUSTIFICATIVA_OPCIONAL, ids=_ids)
@pytest.mark.parametrize("ausente", [None, "", "   "])
def test_justificativa_opcional_ausente_vira_none(de: S, evento: E, ausente: str | None) -> None:
    assert transition(de, evento, **_com(de, evento, justificativa=ausente)).justificativa is None


@pytest.mark.parametrize(("de", "evento"), _MAQUINA, ids=_ids)
def test_justificativa_proibida_para_worker_e_system(de: S, evento: E) -> None:
    assert _erros(de, evento, **_com(de, evento, justificativa=_JUSTIFICATIVA)) == [
        ("justificativa", ErrorCode.NOT_APPLICABLE, {})
    ]
    assert _erros(de, evento, **_com(de, evento, justificativa=".")) == [
        ("justificativa", ErrorCode.NOT_APPLICABLE, {})
    ]
    assert transition(de, evento, **_com(de, evento, justificativa="  ")).justificativa is None


# ---- sap_contract_number (TODO(decisao #13)) ---------------------------------


@pytest.mark.parametrize(("de", "evento"), _COM_NUMERO, ids=_ids)
@pytest.mark.parametrize("ausente", [None, "", "   "])
def test_numero_sap_obrigatorio(de: S, evento: E, ausente: str | None) -> None:
    assert _erros(de, evento, **_com(de, evento, sap_contract_number=ausente)) == [
        ("sap_contract_number", ErrorCode.REQUIRED, {})
    ]


@pytest.mark.parametrize(("de", "evento"), _COM_NUMERO, ids=_ids)
@pytest.mark.parametrize(
    ("numero", "erro"),
    [
        ("40000123456", (ErrorCode.MAX_LENGTH, {"max": 10})),
        ("4000A12345", (ErrorCode.INVALID_FORMAT, {"formato": "somente digitos"})),
        ("40-0012345", (ErrorCode.INVALID_FORMAT, {"formato": "somente digitos"})),
        # digitos arabe-indicos: str.isdigit() aceitaria; so ASCII vale
        ("٤٠٠٠", (ErrorCode.INVALID_FORMAT, {"formato": "somente digitos"})),
        ("40 12", (ErrorCode.INVALID_FORMAT, {"formato": "somente digitos"})),
        (4000012345, (ErrorCode.INVALID_TYPE, {"tipo": "texto"})),
    ],
)
def test_numero_sap_invalido(
    de: S, evento: E, numero: object, erro: tuple[ErrorCode, dict[str, Any]]
) -> None:
    assert _erros(de, evento, **_com(de, evento, sap_contract_number=numero)) == [
        ("sap_contract_number", *erro)
    ]


@pytest.mark.parametrize(("de", "evento"), _COM_NUMERO, ids=_ids)
@pytest.mark.parametrize(
    ("numero", "gravado"),
    [
        ("1", "0000000001"),
        ("40001234", "0040001234"),
        ("0040001234", "0040001234"),
        ("4000012345", "4000012345"),
        (" 40001234 ", "0040001234"),
    ],
)
def test_numero_sap_normalizado_para_vbeln_canonico(
    de: S, evento: E, numero: str, gravado: str
) -> None:
    """VBELN canonico: 10 digitos, zeros a esquerda (TODO(decisao #13): confirmar em DEV)."""
    t = transition(de, evento, **_com(de, evento, sap_contract_number=numero))
    assert t.sap_contract_number == gravado


@pytest.mark.parametrize(("de", "evento"), _COM_NUMERO, ids=_ids)
@pytest.mark.parametrize("numero", ["0", "000", "0000000000", " 00 "])
def test_numero_sap_so_de_zeros_e_invalido(de: S, evento: E, numero: str) -> None:
    assert _erros(de, evento, **_com(de, evento, sap_contract_number=numero)) == [
        ("sap_contract_number", ErrorCode.INVALID_FORMAT, {"formato": "diferente de zero"})
    ]


@pytest.mark.parametrize(("de", "evento"), _COM_NUMERO, ids=_ids)
def test_tamanho_e_conferido_antes_de_completar_com_zeros(de: S, evento: E) -> None:
    """11 caracteres e erro mesmo que a forma canonica caiba em 10 (TODO(decisao #13))."""
    assert _erros(de, evento, **_com(de, evento, sap_contract_number="00040001234")) == [
        ("sap_contract_number", ErrorCode.MAX_LENGTH, {"max": 10})
    ]


@pytest.mark.parametrize(("de", "evento"), _COM_NUMERO, ids=_ids)
def test_numero_com_e_sem_zeros_a_esquerda_geram_o_mesmo_registro(de: S, evento: E) -> None:
    curto = transition(de, evento, **_com(de, evento, sap_contract_number="40001234"))
    longo = transition(de, evento, **_com(de, evento, sap_contract_number="0040001234"))
    assert curto == longo
    assert curto.sap_contract_number == "0040001234"


@pytest.mark.parametrize(("de", "evento"), _SEM_NUMERO, ids=_ids)
def test_numero_sap_sobrando_e_erro(de: S, evento: E) -> None:
    assert _erros(de, evento, **_com(de, evento, sap_contract_number=_NUMERO_SAP)) == [
        ("sap_contract_number", ErrorCode.NOT_APPLICABLE, {})
    ]
    t = transition(de, evento, **_com(de, evento, sap_contract_number="  "))
    assert t.sap_contract_number is None


# ---- detalhe (so worker/system, sem texto livre) -----------------------------


@pytest.mark.parametrize(("de", "evento"), _MAQUINA, ids=_ids)
def test_detalhe_de_worker_e_system_e_gravado_imutavel(de: S, evento: E) -> None:
    original: dict[str, str | int] = {"tentativa": 2, "erro_class": "ConnectError"}
    t = transition(de, evento, **_com(de, evento, detalhe=original))
    assert dict(t.detalhe) == {"tentativa": 2, "erro_class": "ConnectError"}
    original["tentativa"] = 99
    assert t.detalhe["tentativa"] == 2
    with pytest.raises(TypeError):
        t.detalhe["x"] = 1  # type: ignore[index]


@pytest.mark.parametrize(("de", "evento"), _HUMANO, ids=_ids)
def test_detalhe_nao_se_aplica_a_user_e_admin(de: S, evento: E) -> None:
    assert _erros(de, evento, **_com(de, evento, detalhe={"a": 1})) == [
        ("detalhe", ErrorCode.NOT_APPLICABLE, {})
    ]
    assert transition(de, evento, **_com(de, evento, detalhe={})).detalhe == {}


def _erros_detalhe(detalhe: object) -> list[tuple[str, ErrorCode, dict[str, Any]]]:
    return _erros(S.ENVIANDO, E.FALHA_ANTES_POST, ator=_ATORES[K.WORKER], detalhe=detalhe)


@pytest.mark.parametrize("detalhe", [[("a", 1)], "a=1", 1])
def test_detalhe_precisa_ser_mapping(detalhe: object) -> None:
    assert _erros_detalhe(detalhe) == [("detalhe", ErrorCode.INVALID_TYPE, {"tipo": "objeto"})]


@pytest.mark.parametrize("chave", ["", "Erro", "1a", "_a", "a-b", "a b", "á", "a" * 41, 1, None])
def test_chave_do_detalhe_e_snake_case_curta(chave: object) -> None:
    assert _erros_detalhe({chave: 1}) == [
        (
            "detalhe",
            ErrorCode.INVALID_FORMAT,
            {"formato": "chave snake_case com ate 40 caracteres"},
        )
    ]


@pytest.mark.parametrize("chave", ["a", "a1", "erro_class", "a" * 40, "a_"])
def test_chave_do_detalhe_valida(chave: str) -> None:
    t = transition(S.ENVIANDO, E.FALHA_ANTES_POST, ator=_ATORES[K.WORKER], detalhe={chave: 0})
    assert dict(t.detalhe) == {chave: 0}


@pytest.mark.parametrize(
    ("valor", "erro"),
    [
        (True, (ErrorCode.INVALID_TYPE, {"tipo": "texto ou inteiro"})),
        (1.5, (ErrorCode.INVALID_TYPE, {"tipo": "texto ou inteiro"})),
        (None, (ErrorCode.INVALID_TYPE, {"tipo": "texto ou inteiro"})),
        ({"b": 1}, (ErrorCode.INVALID_TYPE, {"tipo": "texto ou inteiro"})),
        ("x" * 201, (ErrorCode.MAX_LENGTH, {"max": 200})),
    ],
)
def test_valor_do_detalhe_invalido(valor: object, erro: tuple[ErrorCode, dict[str, Any]]) -> None:
    assert _erros_detalhe({"erro_class": valor}) == [("detalhe.erro_class", *erro)]


@pytest.mark.parametrize("valor", ["", "x" * 200, 0, -1, 10**12])
def test_valor_do_detalhe_valido(valor: str | int) -> None:
    t = transition(S.ENVIANDO, E.FALHA_ANTES_POST, ator=_ATORES[K.WORKER], detalhe={"v": valor})
    assert t.detalhe["v"] == valor


def test_detalhe_aponta_todos_os_problemas() -> None:
    assert _erros_detalhe({"ok": 1, "Ruim": 1, "valor": 1.5, "longo": "x" * 201}) == [
        (
            "detalhe",
            ErrorCode.INVALID_FORMAT,
            {"formato": "chave snake_case com ate 40 caracteres"},
        ),
        ("detalhe.valor", ErrorCode.INVALID_TYPE, {"tipo": "texto ou inteiro"}),
        ("detalhe.longo", ErrorCode.MAX_LENGTH, {"max": 200}),
    ]


def test_detalhe_ausente_e_mapping_vazio() -> None:
    t = transition(S.ENVIANDO, E.FALHA_ANTES_POST, ator=_ATORES[K.WORKER])
    assert isinstance(t.detalhe, Mapping)
    assert dict(t.detalhe) == {}


# ---- Acumulacao, imutabilidade, pureza ---------------------------------------


def test_erros_acumulam_numa_excecao_so_em_ordem_fixa() -> None:
    assert _erros(
        S.ENVIANDO,
        E.SAP_201,
        ator=Ator(K.ADMIN, " "),
        justificativa=_JUSTIFICATIVA,
        sap_contract_number="12A",
        detalhe={"X": 1},
    ) == [
        ("ator.kind", ErrorCode.ACTOR_NOT_ALLOWED, {"esperado": "worker", "recebido": "admin"}),
        ("ator.identifier", ErrorCode.REQUIRED, {}),
        ("justificativa", ErrorCode.NOT_APPLICABLE, {}),
        ("sap_contract_number", ErrorCode.INVALID_FORMAT, {"formato": "somente digitos"}),
        (
            "detalhe",
            ErrorCode.INVALID_FORMAT,
            {"formato": "chave snake_case com ate 40 caracteres"},
        ),
    ]


def test_transicao_e_imutavel() -> None:
    t = transition(S.RASCUNHO, E.SUBMETER, ator=_ATORES[K.USER])
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.para = S.CRIADO  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        _ATORES[K.USER].identifier = "x"  # type: ignore[misc]


def test_transicao_e_deterministica() -> None:
    kwargs = _com(S.INCERTO, E.RECONCILIAR_PARA_CRIADO)
    assert transition(S.INCERTO, E.RECONCILIAR_PARA_CRIADO, **kwargs) == transition(
        S.INCERTO, E.RECONCILIAR_PARA_CRIADO, **kwargs
    )


def test_transicao_nao_tem_timestamp() -> None:
    """occurred_at e carimbado pelo caso de uso via porta Clock (Fase 2/3)."""
    assert [f.name for f in dataclasses.fields(Transicao)] == [
        "de",
        "para",
        "evento",
        "ator",
        "justificativa",
        "sap_contract_number",
        "detalhe",
    ]


# ---- Caracteres invalidos (B4: o Postgres recusa \x00 em text) ---------------

_CONTROLE = ["\x00", "\x07", "\x1b", "\x7f", "\x85", chr(0xD800), chr(0xDFFF)]
_ids_char = lambda c: f"U+{ord(c):04X}"  # noqa: E731


@pytest.mark.parametrize("ruim", [*_CONTROLE, "\t", "\n", "\r"], ids=_ids_char)
def test_identifier_recusa_controle_e_surrogate(ruim: str) -> None:
    ator = Ator(K.USER, f"oid{ruim}123")
    assert _erros(S.RASCUNHO, E.SUBMETER, ator=ator) == [
        ("ator.identifier", ErrorCode.INVALID_CHARACTERS, {})
    ]


@pytest.mark.parametrize("ruim", _CONTROLE, ids=_ids_char)
@pytest.mark.parametrize(("de", "evento"), _COM_JUSTIFICATIVA + _JUSTIFICATIVA_OPCIONAL, ids=_ids)
def test_justificativa_recusa_controle_e_surrogate(de: S, evento: E, ruim: str) -> None:
    texto = f"Conferido na VA43{ruim} com o comercial."
    assert _erros(de, evento, **_com(de, evento, justificativa=texto)) == [
        ("justificativa", ErrorCode.INVALID_CHARACTERS, {})
    ]


def test_justificativa_aceita_tab_e_quebra_de_linha() -> None:
    texto = "Conferido na VA43.\r\nContrato 40001234\tnao existe."
    t = transition(S.INCERTO, E.CANCELAR, ator=_ATORES[K.ADMIN], justificativa=texto)
    assert t.justificativa == texto


def test_caractere_invalido_na_justificativa_vem_antes_do_tamanho() -> None:
    assert _erros(S.INCERTO, E.CANCELAR, ator=_ATORES[K.ADMIN], justificativa="curta\x00") == [
        ("justificativa", ErrorCode.INVALID_CHARACTERS, {})
    ]


@pytest.mark.parametrize("ruim", [*_CONTROLE, "\t", "\n", "\r"], ids=_ids_char)
def test_valor_texto_do_detalhe_recusa_controle_e_surrogate(ruim: str) -> None:
    assert _erros_detalhe({"erro_class": f"Connect{ruim}Error"}) == [
        ("detalhe.erro_class", ErrorCode.INVALID_CHARACTERS, {})
    ]


def test_caractere_invalido_no_detalhe_vem_antes_do_tamanho() -> None:
    assert _erros_detalhe({"erro_class": "\x00" + "x" * 300}) == [
        ("detalhe.erro_class", ErrorCode.INVALID_CHARACTERS, {})
    ]
