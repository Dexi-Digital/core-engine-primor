"""Tests para o resolver de DATABASE_URL do alembic env.py.

A logica vive em `app.core.db_url.resolve_sync_database_url` e e reusada
por `alembic/env.py::_resolve_database_url`. Testamos o helper direto para
nao duplicar a logica -- se a regra mudar num lado, ambos atualizam juntos.
"""
from __future__ import annotations

import pytest

from app.core.db_url import (
    DatabaseUrlNotConfiguredError,
    resolve_sync_database_url,
)


def test_async_to_sync() -> None:
    out = resolve_sync_database_url("postgresql+asyncpg://u:p@h:5432/db")
    assert out == "postgresql+psycopg2://u:p@h:5432/db"


def test_plain_postgresql_gets_psycopg2() -> None:
    out = resolve_sync_database_url("postgresql://u:p@h:5432/db")
    assert out == "postgresql+psycopg2://u:p@h:5432/db"


def test_legacy_postgres_scheme_normalized() -> None:
    # Heroku/Render legado mandam `postgres://`; SQLAlchemy 2.x recusa.
    out = resolve_sync_database_url("postgres://u:p@h:5432/db")
    assert out == "postgresql+psycopg2://u:p@h:5432/db"


def test_psycopg2_passthrough() -> None:
    out = resolve_sync_database_url("postgresql+psycopg2://u:p@h:5432/db")
    assert out == "postgresql+psycopg2://u:p@h:5432/db"


def test_env_overrides_fallback() -> None:
    out = resolve_sync_database_url(
        env_value="postgresql+asyncpg://prod-user:prod-pwd@prod-host:5432/proddb",
        fallback="postgresql+psycopg2://primor:primor@localhost:5432/primor",
    )
    assert out == "postgresql+psycopg2://prod-user:prod-pwd@prod-host:5432/proddb"


def test_fallback_when_env_missing() -> None:
    out = resolve_sync_database_url(
        env_value=None,
        fallback="postgresql+psycopg2://primor:primor@localhost:5432/primor",
    )
    assert out == "postgresql+psycopg2://primor:primor@localhost:5432/primor"


def test_raises_when_both_missing() -> None:
    with pytest.raises(DatabaseUrlNotConfiguredError):
        resolve_sync_database_url(env_value=None, fallback=None)
