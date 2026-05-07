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
