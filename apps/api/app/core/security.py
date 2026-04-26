"""Password hashing and JWT helpers.

JWT assina com `settings.secret_key` (HS256). Em dev, o default
`change-me-in-production` dispara um warning no startup -- ver
`app.modules.auth.startup.warn_dev_secret`.

Tokens:
- access (`type=access`, default 30min): autentica request
- refresh (`type=refresh`, default 7d): troca por novo access em /auth/refresh
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"

# Tipos de token (sempre dentro do payload `type`).
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

# bcrypt 4+/5+ rejeita secrets > 72 bytes em vez de truncar como antes.
# Truncamos explicitamente para manter compat com senhas longas (UTF-8
# em portugues facilmente passa de 72 bytes em frases).
_BCRYPT_MAX_BYTES = 72


def _to_bytes(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_to_bytes(plain), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Hash mal-formada (ex.: registro corrompido, dummy invalido) ->
        # falha graceful em vez de quebrar o request.
        return False


def _encode(subject: str, *, token_type: str, expires_delta: timedelta, extra: dict[str, Any] | None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(
    subject: str, extra: dict[str, Any] | None = None
) -> str:
    settings = get_settings()
    return _encode(
        subject,
        token_type=TOKEN_TYPE_ACCESS,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        extra=extra,
    )


def create_refresh_token(
    subject: str, extra: dict[str, Any] | None = None
) -> str:
    settings = get_settings()
    return _encode(
        subject,
        token_type=TOKEN_TYPE_REFRESH,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
        extra=extra,
    )


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
