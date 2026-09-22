"""Gate de teste de mutacao: zero mutantes nao-mortos fora da lista de equivalentes.

Uso (POSIX; no Windows so via WSL), depois de ``uv run mutmut run``:
    uv run python scripts/mutation_gate.py

Le ``mutmut results`` (que lista os mutantes que NAO foram mortos, no formato
``    <nome>: <status>``). Qualquer status diferente de ``killed`` reprova, a
menos que o nome esteja em ``mutation-equivalentes.txt`` com justificativa.
Para cada reprovado, imprime o diff (``mutmut show``). Tambem reprova se o
mutmut nao gerou nenhum mutante (gate vazio nao conta como verde).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
EQUIVALENTES = BACKEND_DIR / "mutation-equivalentes.txt"
_LINHA = re.compile(r"^\s+(?P<nome>\S+): (?P<status>.+?)\s*$")


def _mutmut(*args: str) -> str:
    r = subprocess.run(  # noqa: S603 — comando fixo, sem entrada externa
        ["mutmut", *args],  # noqa: S607 — mutmut do venv (uv run)
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout


def _status(saida: str) -> dict[str, str]:
    return {m["nome"]: m["status"] for linha in saida.splitlines() if (m := _LINHA.match(linha))}


def _equivalentes() -> dict[str, str]:
    """``nome  # justificativa`` por linha; justificativa obrigatoria."""
    if not EQUIVALENTES.exists():
        return {}
    resultado: dict[str, str] = {}
    for n, linha in enumerate(EQUIVALENTES.read_text(encoding="utf-8").splitlines(), 1):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        nome, _, justificativa = linha.partition("#")
        if not justificativa.strip():
            raise SystemExit(
                f"{EQUIVALENTES.name}:{n}: equivalente sem justificativa: {nome.strip()}"
            )
        resultado[nome.strip()] = justificativa.strip()
    return resultado


def main() -> int:
    todos = _status(_mutmut("results", "--all", "true"))
    if not todos:
        print("mutation gate FALHOU: o mutmut nao gerou nenhum mutante (config errada?)")
        return 1
    nao_mortos = {n: s for n, s in todos.items() if s != "killed"}
    equivalentes = _equivalentes()

    reprovados = {n: s for n, s in nao_mortos.items() if n not in equivalentes}
    aceitos = {n: s for n, s in nao_mortos.items() if n in equivalentes}
    obsoletos = sorted(set(equivalentes) - set(todos))

    print(
        f"mutantes: {len(todos)} | mortos: {len(todos) - len(nao_mortos)} | "
        f"equivalentes aceitos: {len(aceitos)} | reprovados: {len(reprovados)}"
    )
    for nome in sorted(aceitos):
        print(f"  equivalente: {nome} — {equivalentes[nome]}")
    for nome in obsoletos:
        print(f"  aviso: equivalente listado nao existe mais: {nome}")
    for nome, status in sorted(reprovados.items()):
        print(f"\n### {nome}: {status}")
        print(_mutmut("show", nome))

    if reprovados:
        print(f"\nmutation gate FALHOU: {len(reprovados)} mutante(s) nao morto(s)")
        return 1
    print("mutation gate OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
