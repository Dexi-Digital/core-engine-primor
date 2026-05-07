"""Alembic migration environment.

DATABASE_URL e lido do ambiente em prod (Render/Railway/Fly/DO injetam a env)
e tem precedencia sobre o `sqlalchemy.url` do alembic.ini -- esse ultimo so
serve como fallback quando alguem roda `alembic` direto na maquina sem env
(ex: gerar migration nova localmente).

A URL e normalizada de async (`+asyncpg`) para sync (`+psycopg2`) porque
o alembic usa `sqlalchemy.engine_from_config` sincrono.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Importar os models garante que estejam registrados no metadata:
from app.audit import models as _audit  # noqa: F401
from app.core.db import Base
from app.modules.auth import models as _auth  # noqa: F401
from app.modules.diagnostico import models as _diag  # noqa: F401
from app.modules.dp_sesmt import afastamentos as _afast  # noqa: F401
from app.modules.dp_sesmt import models as _dp  # noqa: F401
from app.modules.fiscal import models as _fiscal  # noqa: F401
from app.modules.licitacoes import models as _lic  # noqa: F401
from app.modules.manutencao_frota import models as _frota  # noqa: F401
from app.modules.obras import models as _obras  # noqa: F401
from app.modules.onedrive_sync import models as _onedrive  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_database_url() -> str:
    """Resolve DATABASE_URL: env > alembic.ini, normalizando driver async->sync.

    Aceita os formatos comuns que aparecem nos providers:
      postgres://...               (Heroku/Render legado)
      postgresql://...             (padrao)
      postgresql+asyncpg://...     (o que a app usa em runtime)
      postgresql+psycopg2://...    (o que o alembic precisa)

    Sempre devolve `postgresql+psycopg2://...`.
    """
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "DATABASE_URL nao configurada (env e alembic.ini vazios)."
        )
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql+psycopg2://" + url[len("postgresql+asyncpg://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = _resolve_database_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = _resolve_database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
