# syntax=docker/dockerfile:1
# Imagem unica do backend: api (default) e worker (sobrescreve o CMD).
#   api:    docker run IMG
#   worker: docker run IMG app.entrypoints.worker

# ---- builder: resolve e instala deps + projeto no /app/.venv ---------------
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_DEV=1

WORKDIR /app

# 1) So dependencias (camada cacheada enquanto uv.lock nao mudar)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable

# 2) Projeto (instalado nao-editavel: o runtime so precisa do .venv)
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

# ---- runtime: python slim + .venv, usuario nao-root ------------------------
FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --no-create-home appuser

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER appuser
EXPOSE 8000

# Sem HEALTHCHECK na imagem: ela serve api E worker, e o worker nao tem HTTP
# (herdaria um check que falha -> restart em loop no Swarm). O healthcheck e
# declarado por servico: compose (infra/compose.dev.yml, so na api) e stack.

ENTRYPOINT ["python", "-m"]
CMD ["app.entrypoints.api"]
