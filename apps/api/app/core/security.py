"""Password hashing and JWT helpers."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_settings = get_settings()


def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": datetime.now(UTC) + timedelta(minutes=_settings.access_token_expire_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, _settings.secret_key, algorithms=["HS256"])
