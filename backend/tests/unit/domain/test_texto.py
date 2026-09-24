"""Regra unica de caracteres invalidos (contrato e maquina de estados)."""

from __future__ import annotations

import pytest

from app.domain.texto import tem_caractere_invalido


@pytest.mark.parametrize(
    "ch",
    ["\x00", "\x01", "\x1f", "\x7f", "\x80", "\x85", "\x9f", chr(0xD800), chr(0xDBFF), chr(0xDFFF)],
    ids=lambda c: f"U+{ord(c):04X}",
)
@pytest.mark.parametrize("multilinha", [False, True])
def test_controle_e_surrogate_sao_invalidos(ch: str, multilinha: bool) -> None:
    assert tem_caractere_invalido(f"a{ch}b", multilinha=multilinha)


@pytest.mark.parametrize("ch", ["\t", "\n", "\r"], ids=lambda c: f"U+{ord(c):04X}")
def test_tab_e_quebras_so_em_multilinha(ch: str) -> None:
    assert tem_caractere_invalido(f"a{ch}b", multilinha=False)
    assert not tem_caractere_invalido(f"a{ch}b", multilinha=True)


@pytest.mark.parametrize(
    "texto",
    ["", "abc", "Paranagu\u00e1", "a\u200bb", "\U0001f600", "\u00a0", "\u2028", "\ufeff"],
)
def test_imprimivel_formatacao_e_separadores_sao_validos(texto: str) -> None:
    """Cf (\u200b, \ufeff), Zs (\u00a0) e Zl (\u2028) nao sao Cc nem Cs."""
    assert not tem_caractere_invalido(texto, multilinha=False)


def test_so_o_vertical_tab_e_form_feed_ficam_de_fora_mesmo_em_multilinha() -> None:
    assert tem_caractere_invalido("a\x0bb", multilinha=True)
    assert tem_caractere_invalido("a\x0cb", multilinha=True)
