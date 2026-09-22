"""Entrypoint da API (``python -m app.entrypoints.api``)."""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 — roda em container; exposicao controlada pelo Traefik
        port=8000,
        log_config=None,  # mantem o JSON do configure_logging (uvicorn nao sobrescreve)
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
