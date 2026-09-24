"""Nenhum arquivo de texto do repo tem byte de controle (exceto tab, LF e CR).

Um NUL literal chegou a ser commitado na ARCHITECTURE por uma edicao
automatizada; este teste impede que isso passe de novo sem ser visto.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_CONTROLE = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EXTENSOES = {
    ".py",
    ".md",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".xml",
    ".ts",
    ".tsx",
    ".cfg",
    ".txt",
}
_IGNORAR = {".git", ".venv", "node_modules", "dist", ".mypy_cache", ".ruff_cache", "mutants"}


def _raiz() -> Path:
    for pasta in Path(__file__).resolve().parents:
        if (pasta / "CLAUDE.md").is_file() and (pasta / "docs").is_dir():
            return pasta
    raise FileNotFoundError("raiz do repo nao encontrada acima de " + __file__)


def _arquivos_de_texto() -> list[Path]:
    arquivos: list[Path] = []
    for pasta, subpastas, nomes in os.walk(_raiz()):
        subpastas[:] = [d for d in subpastas if d not in _IGNORAR]  # poda: nao entra
        arquivos += [Path(pasta, n) for n in nomes if Path(n).suffix in _EXTENSOES]
    return arquivos


def test_ha_arquivos_para_varrer() -> None:
    nomes = {p.name for p in _arquivos_de_texto()}
    assert {"ARCHITECTURE.md", "CLAUDE.md", "contract.py"} <= nomes


def test_nenhum_arquivo_de_texto_tem_byte_de_controle() -> None:
    ruins = {
        str(p.relative_to(_raiz())): len(_CONTROLE.findall(p.read_bytes()))
        for p in _arquivos_de_texto()
        if _CONTROLE.search(p.read_bytes())
    }
    assert ruins == {}
