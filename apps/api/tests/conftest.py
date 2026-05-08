"""Test fixtures: in-memory SQLite + async session override for the FastAPI app."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base, get_db
from app.main import app


@pytest.fixture(autouse=True)
def _disable_login_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Desabilita o rate limit do login em todos os testes.

    Os testes nao sobem Redis; com o limiter habilitado, toda chamada
    de `auth_headers` tenta conectar no `redis://localhost:6379` e
    trava ate timeout (mesmo no fail-open). Quem quiser EXERCITAR o
    limiter usa `monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "1")`
    + `get_settings.cache_clear()`.
    """
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "0")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def api_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


# --- Auth helpers ----------------------------------------------------------
#
# A maioria dos endpoints que muta recurso (POST/PUT/PATCH/DELETE) agora
# exige `Depends(get_current_user)` para gravar `actor` real no audit_log
# (LGPD / AGENTS.md). Para tests que precisam atravessar esses guards,
# use a fixture `auth_headers` -- ela cria um admin de teste e devolve
# `{"Authorization": "Bearer <jwt>"}` pra anexar nos requests.
#
# Tests que verificam comportamento de 401 NAO devem usar essa fixture
# -- continuam chamando `api_client` sem header.


_TEST_ADMIN_EMAIL = "test-admin@primor.com"
_TEST_ADMIN_PASSWORD = "hunter22zz"


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession):
    """Cria um admin de teste no DB.

    Cria via `auth_service.create_user` para passar pelos validadores
    (hash de senha, normalizacao de email, audit do create). O actor
    do audit e `system:test-fixture` -- sao mutacoes geradas pelo
    test setup, nao por usuario logado.
    """
    from app.modules.auth import service as auth_service

    return await auth_service.create_user(
        db_session,
        email=_TEST_ADMIN_EMAIL,
        nome="Test Admin",
        password=_TEST_ADMIN_PASSWORD,
        role="admin",
        module_roles=None,
        is_active=True,
        actor="system:test-fixture",
    )


@pytest_asyncio.fixture
async def auth_headers(admin_user, api_client: AsyncClient) -> dict[str, str]:
    """Header `Authorization: Bearer <jwt>` para um admin de teste.

    Use em qualquer chamada que muta recurso -- esses endpoints exigem
    JWT para gravar actor real no audit_log. Tests que verificam o
    caminho de 401 (sem token / token expirado / token invalido) NAO
    devem usar essa fixture.
    """
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": _TEST_ADMIN_EMAIL, "password": _TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def anyio_backend() -> str:  # pragma: no cover - compat
    return "asyncio"
