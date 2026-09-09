"""Normalizacao da DATABASE_URL para o driver async (runtime).

Motivacao concreta: Railway, Render e Heroku-likes INJETAM a
`DATABASE_URL` automaticamente no formato `postgresql://...` (o Heroku
ainda usa o legado `postgres://`). O runtime usa asyncpg, que exige
`postgresql+asyncpg://` -- sem normalizar, a app nao sobe nessas
plataformas e o erro nao diz o que fazer.
"""
from __future__ import annotations

import pytest

from app.core.db_url import (
    DatabaseUrlNotConfiguredError,
    resolve_async_database_url,
)


def test_postgresql_puro_ganha_o_driver_async():
    """Formato que Railway e Render injetam sozinhos."""
    assert resolve_async_database_url("postgresql://u:p@h:5432/db") == (
        "postgresql+asyncpg://u:p@h:5432/db"
    )


def test_postgres_legado_do_heroku():
    assert resolve_async_database_url("postgres://u:p@h:5432/db") == (
        "postgresql+asyncpg://u:p@h:5432/db"
    )


def test_psycopg2_vira_asyncpg():
    """A mesma string usada pelo Alembic serve para o runtime."""
    assert resolve_async_database_url("postgresql+psycopg2://u:p@h/db") == (
        "postgresql+asyncpg://u:p@h/db"
    )


def test_ja_async_passa_intacto():
    url = "postgresql+asyncpg://u:p@h/db"
    assert resolve_async_database_url(url) == url


def test_sqlite_de_teste_nao_e_tocado():
    url = "sqlite+aiosqlite:///:memory:"
    assert resolve_async_database_url(url) == url


def test_sslmode_vira_ssl_para_o_asyncpg():
    """Pegadinha ja documentada em docs/deployment-vercel.md: o Neon (e
    o Railway, e o Render) entregam a string com `sslmode=require`, que
    o psycopg2 entende e o asyncpg NAO -- ele quer `ssl=`. Sem essa
    conversao, o deploy quebra com erro obscuro de parametro."""
    assert resolve_async_database_url(
        "postgresql://u:p@h/db?sslmode=require"
    ) == "postgresql+asyncpg://u:p@h/db?ssl=require"


def test_sslmode_no_meio_de_outros_parametros():
    saida = resolve_async_database_url(
        "postgresql://u:p@h/db?application_name=motor&sslmode=require&x=1"
    )
    assert "ssl=require" in saida
    assert "sslmode" not in saida
    assert "application_name=motor" in saida
    assert "x=1" in saida


def test_ssl_ja_correto_nao_e_duplicado():
    saida = resolve_async_database_url("postgresql+asyncpg://u:p@h/db?ssl=require")
    assert saida.count("ssl=require") == 1
    assert "sslmode" not in saida


def test_fallback_e_erro_quando_vazio():
    assert resolve_async_database_url(None, "postgresql://u:p@h/db") == (
        "postgresql+asyncpg://u:p@h/db"
    )
    with pytest.raises(DatabaseUrlNotConfiguredError):
        resolve_async_database_url(None, None)
