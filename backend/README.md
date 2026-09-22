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
uv run lint-imports        # habilitado a partir da Tarefa 0.6
uv run pytest
```

## Gate de cobertura (CI e fechamento de fase)

```bash
uv run pytest \
  --cov=app.domain --cov=app.application \
  --cov-report=term-missing --cov-fail-under=90
```

Domínio + aplicação são medidos em conjunto; API/infra ficam de fora do gate.

## Pendências previstas

- Perfis do Hypothesis (`dev`, `ci` com `derandomize=True`) serão registrados em
  `tests/conftest.py` a partir da Tarefa 1.x, quando houver testes de propriedade.
- Contratos do `import-linter` entram na Tarefa 0.6.
