"""Tests para o resolver de DATABASE_URL do alembic env.py.

A logica esta em `apps/api/alembic/env.py:_resolve_database_url`. Nao
podemos importar diretamente porque o modulo so corre dentro de um
contexto Alembic; copiamos a logica em um helper aqui (com paridade
verificada manualmente) e testamos o helper.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest


@contextmanager
def _env(key: str, value: str | None):
    prev = os.environ.get(key)
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


def _resolve(env_value: str | None, fallback: str | None = None) -> str:
    """Replica de `apps/api/alembic/env.py::_resolve_database_url`.

    Mantenha em sync com o original. As 4 normalizacoes sao:
      postgres://         -> postgresql+psycopg2://
      postgresql://       -> postgresql+psycopg2://
      postgresql+asyncpg  -> postgresql+psycopg2://
      postgresql+psycopg2 -> idempotente (passa direto)
    """
    url = env_value or fallback
    if not url:
        raise RuntimeError("vazio")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql+psycopg2://" + url[len("postgresql+asyncpg://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


def test_async_to_sync() -> None:
    out = _resolve("postgresql+asyncpg://u:p@h:5432/db")
    assert out == "postgresql+psycopg2://u:p@h:5432/db"


def test_plain_postgresql_gets_psycopg2() -> None:
    out = _resolve("postgresql://u:p@h:5432/db")
    assert out == "postgresql+psycopg2://u:p@h:5432/db"


def test_legacy_postgres_scheme_normalized() -> None:
    # Heroku/Render legado mandam `postgres://`; SQLAlchemy 2.x recusa.
    out = _resolve("postgres://u:p@h:5432/db")
    assert out == "postgresql+psycopg2://u:p@h:5432/db"


def test_psycopg2_passthrough() -> None:
    out = _resolve("postgresql+psycopg2://u:p@h:5432/db")
    assert out == "postgresql+psycopg2://u:p@h:5432/db"


def test_env_overrides_fallback() -> None:
    out = _resolve(
        env_value="postgresql+asyncpg://prod-user:prod-pwd@prod-host:5432/proddb",
        fallback="postgresql+psycopg2://primor:primor@localhost:5432/primor",
    )
    assert out == "postgresql+psycopg2://prod-user:prod-pwd@prod-host:5432/proddb"


def test_fallback_when_env_missing() -> None:
    out = _resolve(
        env_value=None,
        fallback="postgresql+psycopg2://primor:primor@localhost:5432/primor",
    )
    assert out == "postgresql+psycopg2://primor:primor@localhost:5432/primor"


def test_raises_when_both_missing() -> None:
    with pytest.raises(RuntimeError):
        _resolve(env_value=None, fallback=None)
