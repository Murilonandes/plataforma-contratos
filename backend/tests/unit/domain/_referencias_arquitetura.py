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


# ---- Classificacao de falhas em runtime (§4, Tarefa 2.7) ------------------------------------


@dataclass(frozen=True)
class LinhaTransporte:
    situacao: str
    excecoes: tuple[str, ...]  # nomes das classes httpx citadas (sem o prefixo ``httpx.``)
    citados: tuple[str, ...]  # tudo que aparece em crase MAIUSCULA na coluna Evento


_EXCECAO = re.compile(r"`(?:httpx\.)?([A-Z][A-Za-z]*(?:Error|Timeout))`")


def _secao(inicio: str, fim: str) -> str:
    texto = achar_architecture().read_text(encoding="utf-8")
    a = texto.index(inicio)
    return texto[a : texto.index(fim, a)]


@cache
def linhas_transporte() -> tuple[LinhaTransporte, ...]:
    """Linhas da tabela 'Exceções de transporte' do §4, na ordem do documento."""
    linhas: list[LinhaTransporte] = []
    for linha in _secao("**Exceções de transporte:**", "**Respostas HTTP").splitlines():
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if len(celulas) < 3 or celulas[0] in {"Situação", "---"}:
            continue
        linhas.append(
            LinhaTransporte(
                situacao=celulas[0],
                excecoes=tuple(dict.fromkeys(re.findall(_EXCECAO, celulas[1]))),
                citados=tuple(re.findall(r"`([A-Z][A-Z0-9_]+)`", celulas[2])),
            )
        )
    return tuple(linhas)


@cache
def regras_precedencia() -> dict[int, str]:
    """Texto de cada item numerado da lista 'Respostas HTTP — precedência de 4xx'."""
    regras: dict[int, str] = {}
    for linha in _secao("**Respostas HTTP", "**Caso especial CSRF 403.**").splitlines():
        achado = re.match(r"^(\d+)\. (.+)$", linha.strip())
        if achado:
            regras[int(achado.group(1))] = achado.group(2)
    return regras


def status_da_regra(n: int) -> frozenset[int]:
    """O conjunto ``{a, b, ...}`` citado na regra ``n``."""
    achado = re.search(r"\{([\d, ]+)\}", regras_precedencia()[n])
    if achado is None:
        raise ValueError(f"regra {n} sem conjunto de status")
    return frozenset(int(s) for s in achado.group(1).split(","))


def eventos_da_regra(n: int) -> tuple[str, ...]:
    return tuple(re.findall(r"`([A-Z][A-Z0-9_]+)`", regras_precedencia()[n]))
