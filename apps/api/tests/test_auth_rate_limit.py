"""Tests para o rate limiter do /auth/login."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.modules.auth import rate_limit as rl


def _settings(**overrides) -> Settings:
    base = {
        "secret_key": "x" * 64,
        "login_rate_limit_enabled": True,
        "login_rate_limit_max_attempts": 3,
        "login_rate_limit_window_s": 60,
    }
    base.update(overrides)
    return Settings(**base)


def _fake_request(ip: str = "1.2.3.4", xff: str | None = None):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host=ip),
    )


@pytest.mark.asyncio
async def test_allows_below_threshold(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def _fake_incr(url: str, key: str, window_s: int) -> int:
        calls.append((key, window_s))
        return len(calls)  # 1, 2, 3...

    monkeypatch.setattr(rl, "_incr_with_ttl", _fake_incr)
    req = _fake_request()
    for _ in range(3):
        await rl.enforce_login_rate_limit(
            req, "user@example.com", settings=_settings()
        )
    assert len(calls) == 3
    assert all(k.startswith("auth:login:user@example.com:1.2.3.4") for k, _ in calls)


@pytest.mark.asyncio
async def test_blocks_over_threshold(monkeypatch):
    async def _fake_incr(url: str, key: str, window_s: int) -> int:
        return 99

    monkeypatch.setattr(rl, "_incr_with_ttl", _fake_incr)
    with pytest.raises(HTTPException) as exc:
        await rl.enforce_login_rate_limit(
            _fake_request(), "user@example.com", settings=_settings()
        )
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


@pytest.mark.asyncio
async def test_disabled_is_noop(monkeypatch):
    # Com `enabled=False` nem chegamos a tocar em Redis.
    async def _explode(*_a, **_kw):
        raise AssertionError("nao deveria tentar incrementar")

    monkeypatch.setattr(rl, "_incr_with_ttl", _explode)
    await rl.enforce_login_rate_limit(
        _fake_request(),
        "user@example.com",
        settings=_settings(login_rate_limit_enabled=False),
    )


@pytest.mark.asyncio
async def test_redis_failure_is_fail_open(monkeypatch):
    async def _fail(*_a, **_kw):
        raise ConnectionError("redis down")

    monkeypatch.setattr(rl, "_incr_with_ttl", _fail)
    # Nao deve raise -- fail-open.
    await rl.enforce_login_rate_limit(
        _fake_request(), "user@example.com", settings=_settings()
    )


@pytest.mark.asyncio
async def test_uses_xff_when_present(monkeypatch):
    keys: list[str] = []

    async def _fake_incr(url: str, key: str, window_s: int) -> int:
        keys.append(key)
        return 1

    monkeypatch.setattr(rl, "_incr_with_ttl", _fake_incr)
    await rl.enforce_login_rate_limit(
        _fake_request(ip="10.0.0.1", xff="203.0.113.7, 10.0.0.1"),
        "u@x.com",
        settings=_settings(),
    )
    # Primeiro hop do XFF, nao o proxy.
    assert keys == ["auth:login:u@x.com:203.0.113.7"]


@pytest.mark.asyncio
async def test_email_case_normalized(monkeypatch):
    keys: list[str] = []

    async def _fake_incr(url: str, key: str, window_s: int) -> int:
        keys.append(key)
        return 1

    monkeypatch.setattr(rl, "_incr_with_ttl", _fake_incr)
    req = _fake_request()
    await rl.enforce_login_rate_limit(req, "User@Example.COM", settings=_settings())
    await rl.enforce_login_rate_limit(req, "  user@example.com  ", settings=_settings())
    # Mesma chave -> sem bypass via caixa/espaco.
    assert len(set(keys)) == 1


@pytest.mark.asyncio
async def test_different_emails_have_separate_buckets(monkeypatch):
    keys: list[str] = []

    async def _fake_incr(url: str, key: str, window_s: int) -> int:
        keys.append(key)
        return 1

    monkeypatch.setattr(rl, "_incr_with_ttl", _fake_incr)
    req = _fake_request()
    await rl.enforce_login_rate_limit(req, "a@x.com", settings=_settings())
    await rl.enforce_login_rate_limit(req, "b@x.com", settings=_settings())
    assert len(set(keys)) == 2
