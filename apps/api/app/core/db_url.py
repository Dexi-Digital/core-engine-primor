"""Normalizacao de `DATABASE_URL` compartilhada entre runtime e alembic.

Aceita os formatos comuns que aparecem nos providers:
  postgres://...               (Heroku/Render legado)
  postgresql://...             (padrao)
  postgresql+asyncpg://...     (o que a app usa em runtime)
  postgresql+psycopg2://...    (o que o alembic precisa)

`resolve_sync_database_url` devolve sempre `postgresql+psycopg2://...`.
"""
from __future__ import annotations


class DatabaseUrlNotConfiguredError(RuntimeError):
    """`DATABASE_URL` ausente no env e sem fallback."""


def resolve_sync_database_url(
    env_value: str | None, fallback: str | None = None
) -> str:
    """Resolve `DATABASE_URL` para o driver sync (psycopg2).

    Precedencia: `env_value` > `fallback`. Levanta se ambos vazios.
    """
    url = env_value or fallback
    if not url:
        raise DatabaseUrlNotConfiguredError(
            "DATABASE_URL nao configurada (env e fallback vazios)."
        )
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql+psycopg2://" + url[len("postgresql+asyncpg://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


def resolve_async_database_url(
    env_value: str | None, fallback: str | None = None
) -> str:
    """Resolve `DATABASE_URL` para o driver async (asyncpg).

    Existe porque Railway, Render e Heroku-likes INJETAM a variavel
    sozinhos no formato `postgresql://...` (Heroku ainda usa o legado
    `postgres://`), e o runtime da app usa asyncpg, que exige
    `postgresql+asyncpg://`. Sem normalizar, a app simplesmente nao sobe
    nessas plataformas -- e o erro nao aponta o caminho.

    Tambem converte `sslmode=` em `ssl=`: os dois drivers usam nomes
    diferentes para o mesmo parametro, e as strings entregues por Neon,
    Railway e Render vem com `sslmode=require`. Essa pegadinha ja estava
    documentada em `docs/deployment-vercel.md`; aqui ela deixa de ser
    algo que o operador precisa lembrar.

    URLs que nao sao Postgres (ex.: `sqlite+aiosqlite://` dos testes)
    passam intactas.
    """
    url = env_value or fallback
    if not url:
        raise DatabaseUrlNotConfiguredError(
            "DATABASE_URL nao configurada (env e fallback vazios)."
        )

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql+psycopg2://"):
        url = "postgresql+asyncpg://" + url[len("postgresql+psycopg2://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]

    if url.startswith("postgresql+asyncpg://") and "sslmode=" in url:
        url = url.replace("sslmode=", "ssl=")

    return url
