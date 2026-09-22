# Backend — Plataforma de Contratos SAP

Requer Python 3.12 e [uv](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
```

## Comandos de dev

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run lint-imports
uv run pytest
```

## Gate de cobertura (CI e fechamento de fase)

```bash
uv run pytest --cov=app.domain --cov=app.application --cov-report=term-missing
uv run python scripts/coverage_gate.py
```

Domínio + aplicação são medidos em conjunto (≥ 90%); API/infra ficam de fora do gate.
Enquanto as duas camadas não têm nenhum statement (Fase 0), o gate é pulado com aviso;
arquivo que nenhum teste importa conta como 0%.

## Notas

- Perfis do Hypothesis em `tests/conftest.py`: `dev` (default) e `ci` (1000 exemplos,
  `derandomize=True`). Para rodar como no CI: `HYPOTHESIS_PROFILE=ci uv run pytest`.
