"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

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

engine = create_async_engine(settings.database_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
