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
from app.core.db_url import resolve_sync_database_url
from app.modules.auth import models as _auth  # noqa: F401
from app.modules.diagnostico import models as _diag  # noqa: F401
from app.modules.dp_sesmt import afastamentos as _afast  # noqa: F401
from app.modules.dp_sesmt import models as _dp  # noqa: F401
from app.modules.financeiro_contratos import models as _contratos  # noqa: F401
from app.modules.fiscal import models as _fiscal  # noqa: F401
from app.modules.licitacoes import models as _lic  # noqa: F401
from app.modules.manutencao_frota import models as _frota  # noqa: F401
from app.modules.obras import models as _obras  # noqa: F401
from app.modules.onedrive_sync import models as _onedrive  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_database_url() -> str:
    """Wrapper in-Alembic em volta de `resolve_sync_database_url`.

    Precedencia: `DATABASE_URL` do env > `sqlalchemy.url` do alembic.ini.
    """
    return resolve_sync_database_url(
        env_value=os.environ.get("DATABASE_URL"),
        fallback=config.get_main_option("sqlalchemy.url"),
    )


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
