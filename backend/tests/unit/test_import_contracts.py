"""Contratos de arquitetura (import-linter) passam.

Roda o import-linter in-process com o ``[tool.importlinter]`` do pyproject;
equivalente a ``uv run lint-imports``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from importlinter import cli

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_contratos_de_import_passam(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(BACKEND_DIR)
    assert Path(os.getcwd(), "pyproject.toml").is_file()
    assert cli.lint_imports(no_cache=True, no_logo=True) == cli.EXIT_STATUS_SUCCESS
