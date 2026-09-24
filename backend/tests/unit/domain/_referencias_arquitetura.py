"""Leitura da matriz de transicoes do ARCHITECTURE §4 para os testes do dominio.

A tabela do ``.md`` e a fonte unica da maquina de estados: ``test_enums`` e
``test_states`` conferem o codigo contra ela. Funciona tambem dentro de
``mutants/`` do mutmut (sobe diretorios ate achar ``docs/ARCHITECTURE.md``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path


def achar_architecture() -> Path:
    for pasta in Path(__file__).resolve().parents:
        candidato = pasta / "docs" / "ARCHITECTURE.md"
        if candidato.is_file():
            return candidato
    raise FileNotFoundError("docs/ARCHITECTURE.md nao encontrado acima de " + __file__)


@dataclass(frozen=True)
class LinhaMatriz:
    de: str
    evento: str
    para: str
    ator: str
    justificativa: bool  # coluna "Justif." == "sim"
    observacao: str


_JUSTIF = {"sim": True, "não": False}
_TOTAL = re.compile(r"\*\*Total: (\d+) transições válidas\.\*\*")


def total_declarado() -> int:
    """O N de '**Total: N transições válidas.**' no §4 (tem que bater com as linhas)."""
    achado = _TOTAL.search(achar_architecture().read_text(encoding="utf-8"))
    if achado is None:
        raise ValueError("linha de total nao encontrada no §4")
    return int(achado.group(1))


@cache
def linhas_matriz() -> tuple[LinhaMatriz, ...]:
    """Cada linha da matriz do §4, na ordem do documento."""
    texto = achar_architecture().read_text(encoding="utf-8")
    inicio = texto.index("**Matriz de transições:**")
    fim = _TOTAL.search(texto, inicio)
    if fim is None:
        raise ValueError("linha '**Total: N transições válidas.**' nao encontrada no §4")
    linhas: list[LinhaMatriz] = []
    for linha in texto[inicio : fim.start()].splitlines():
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if len(celulas) < 6 or celulas[0] in {"De", "---"}:
            continue
        de, evento, para, ator, justif, observacao = celulas[:6]
        linhas.append(LinhaMatriz(de, evento, para, ator, _JUSTIF[justif], observacao))
    return tuple(linhas)
