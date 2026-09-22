"""Smoke: garante que o pacote `app` é importável após `uv sync`."""


def test_app_importa() -> None:
    import app

    assert app.__doc__ is not None
