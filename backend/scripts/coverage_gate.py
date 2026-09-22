"""Gate de cobertura de fase: domain + application >= 90% (medidos em conjunto).

Uso (depois de rodar o pytest com --cov):
    uv run pytest --cov=app.domain --cov=app.application --cov-report=
    uv run python scripts/coverage_gate.py

Se as duas camadas ainda nao tem nenhum statement executavel (Fase 0: so
``__init__`` com docstring), o gate e pulado com aviso explicito. Assim que
existir codigo, os 90% passam a valer sem mudar o CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import coverage

MINIMO = 90.0
BACKEND_DIR = Path(__file__).resolve().parents[1]
CAMADAS = ("app/domain", "app/application")


def _arquivos() -> list[Path]:
    return sorted(p for c in CAMADAS for p in (BACKEND_DIR / c).rglob("*.py"))


def main() -> int:
    cov = coverage.Coverage(data_file=str(BACKEND_DIR / ".coverage"))
    cov.load()

    arquivos = [str(p) for p in _arquivos()]
    statements = sum(len(cov.analysis2(p)[1]) for p in arquivos)
    if statements == 0:
        print(f"coverage gate PULADO: nenhum statement em {', '.join(CAMADAS)} ainda.")
        return 0

    # morfs explicitos: arquivo que nenhum teste importou entra como 0% (em vez
    # de sumir do relatorio e inflar a media).
    total = cov.report(morfs=arquivos, show_missing=True)
    if total < MINIMO:
        print(f"coverage gate FALHOU: {total:.2f}% < {MINIMO:.0f}% em {', '.join(CAMADAS)}")
        return 1
    print(f"coverage gate OK: {total:.2f}% >= {MINIMO:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
