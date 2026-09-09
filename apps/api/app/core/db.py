"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db_url import resolve_async_database_url

settings = get_settings()

# Normaliza a URL antes de criar o engine. Railway, Render e
# Heroku-likes injetam `DATABASE_URL` sozinhos como `postgresql://`
# (ou `postgres://`), que o asyncpg nao aceita, e entregam
# `sslmode=require`, que ele tambem nao entende (quer `ssl=`). Sem isso
# a app nao sobe nessas plataformas -- ver `resolve_async_database_url`.
_database_url = resolve_async_database_url(settings.database_url)

# `VERCEL` e setado automaticamente pela Vercel em build e runtime. Em
# serverless cada invocacao pode ser um processo novo (cold start) --
# um QueuePool persistente nao faz sentido (nada reaproveita) e conflita
# com o pooler do Neon (PgBouncer em modo transaction), que exige
# `statement_cache_size=0` no asyncpg (prepared statements nao
# sobrevivem entre transacoes quando o pooler multiplexa conexoes).
# Fora da Vercel (dev local, Docker, testes) o comportamento e identico
# ao anterior -- NullPool so entra com a env var presente.
_engine_kwargs: dict[str, object] = {"echo": False, "future": True}
if os.getenv("VERCEL"):
    _engine_kwargs["poolclass"] = NullPool
    _engine_kwargs["connect_args"] = {"statement_cache_size": 0}

engine = create_async_engine(_database_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
