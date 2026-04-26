"""Pydantic schemas para auth endpoints."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.auth.models import ROLES_VALIDOS
from app.modules.auth.permissions import validate_module_roles

# --- login / token ----------------------------------------------------------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds ate o access expirar (info para o cliente)


class RefreshRequest(BaseModel):
    refresh_token: str


# --- user CRUD --------------------------------------------------------------


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    nome: str
    role: str
    module_roles: dict | None = None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class _UserMutationBase(BaseModel):
    """Campos comuns entre Create e Update (validador de roles)."""

    @staticmethod
    def _ensure_role_canonico(value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ROLES_VALIDOS:
            raise ValueError(
                f"role invalida: {value!r}. Validas: {sorted(ROLES_VALIDOS)}"
            )
        return value

    @staticmethod
    def _ensure_module_roles(
        value: dict | None,
    ) -> dict | None:
        # Validador delega para `permissions.validate_module_roles` para
        # nao duplicar a lista de modulos conhecidos.
        return validate_module_roles(value)


class UserCreate(_UserMutationBase):
    email: EmailStr
    nome: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    role: str = "leitor"
    module_roles: dict | None = None
    is_active: bool = True

    @field_validator("role")
    @classmethod
    def _v_role(cls, v: str) -> str:
        canonical = cls._ensure_role_canonico(v)
        # role e obrigatorio na criacao -- defendemos contra None mesmo
        # que o type seja str (paranoia: middleware/JSON pode enviar null).
        if canonical is None:
            raise ValueError("role e obrigatoria")
        return canonical

    @field_validator("module_roles")
    @classmethod
    def _v_module_roles(cls, v: dict | None) -> dict | None:
        return cls._ensure_module_roles(v)


class UserUpdate(_UserMutationBase):
    nome: str | None = Field(default=None, min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: str | None = None
    module_roles: dict | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def _v_role(cls, v: str | None) -> str | None:
        return cls._ensure_role_canonico(v)

    @field_validator("module_roles")
    @classmethod
    def _v_module_roles(cls, v: dict | None) -> dict | None:
        return cls._ensure_module_roles(v)


__all__ = [
    "LoginRequest",
    "RefreshRequest",
    "TokenPair",
    "UserCreate",
    "UserRead",
    "UserUpdate",
]
