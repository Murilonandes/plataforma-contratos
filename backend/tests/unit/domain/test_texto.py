"""Regra unica de caracteres invalidos (contrato e maquina de estados)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.domain.texto import invalido_em_linha, invalido_em_multilinha

_AMBAS = [invalido_em_linha, invalido_em_multilinha]


@pytest.mark.parametrize(
    "ch",
    ["\x00", "\x01", "\x1f", "\x7f", "\x80", "\x85", "\x9f", chr(0xD800), chr(0xDBFF), chr(0xDFFF)],
    ids=lambda c: f"U+{ord(c):04X}",
)
@pytest.mark.parametrize("regra", _AMBAS, ids=lambda f: f.__name__)
def test_controle_e_surrogate_sao_invalidos(ch: str, regra: Callable[[str], bool]) -> None:
    assert regra(f"a{ch}b")


@pytest.mark.parametrize("ch", ["\t", "\n", "\r"], ids=lambda c: f"U+{ord(c):04X}")
def test_tab_e_quebras_so_em_multilinha(ch: str) -> None:
    assert invalido_em_linha(f"a{ch}b")
    assert not invalido_em_multilinha(f"a{ch}b")


@pytest.mark.parametrize(
    "texto",
    ["", "abc", "Paranagu\u00e1", "a\u200bb", "\U0001f600", "\u00a0", "\u2028", "\ufeff"],
)
@pytest.mark.parametrize("regra", _AMBAS, ids=lambda f: f.__name__)
def test_imprimivel_formatacao_e_separadores_sao_validos(
    texto: str, regra: Callable[[str], bool]
) -> None:
    """Cf (\u200b, \ufeff), Zs (\u00a0) e Zl (\u2028) nao sao Cc nem Cs."""
    assert not regra(texto)


@pytest.mark.parametrize("ch", ["\x0b", "\x0c"], ids=lambda c: f"U+{ord(c):04X}")
def test_vertical_tab_e_form_feed_sao_invalidos_mesmo_em_multilinha(ch: str) -> None:
    assert invalido_em_multilinha(f"a{ch}b")
