"""ORM model para usuarios autenticados.

RBAC hibrido:

- `role` (global): `admin` | `operador` | `leitor`. Vale como default
  para todos os modulos da plataforma.
- `module_roles` (JSONB, opcional): override por modulo. Ex.:
  `{"rh": "admin", "frota": "leitor"}`. Quando o modulo nao aparece
  no dict, usa o `role` global.

Modulos conhecidos (ver `app.modules.auth.permissions.MODULOS`):
`rh`, `frota`, `fiscal`, `licitacoes`, `financeiro`, `observability`.

Senha: `password_hash` armazena bcrypt via `app.core.security`. Senha
em plain-text NUNCA toca o DB.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Roles canonicos (mais permissivo -> menos permissivo).
ROLE_ADMIN = "admin"
ROLE_OPERADOR = "operador"
ROLE_LEITOR = "leitor"
ROLES_VALIDOS = frozenset({ROLE_ADMIN, ROLE_OPERADOR, ROLE_LEITOR})

# Niveis numericos para comparar permissoes ("admin >= operador >= leitor").
ROLE_LEVEL: dict[str, int] = {
    ROLE_LEITOR: 1,
    ROLE_OPERADOR: 2,
    ROLE_ADMIN: 3,
}


class User(Base):
    """Usuario autenticado da plataforma."""

    __tablename__ = "auth_users"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Email e a chave natural -- normalizamos para lowercase no service.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(255))

    password_hash: Mapped[str] = mapped_column(String(255))

    role: Mapped[str] = mapped_column(String(16), default=ROLE_LEITOR)
    # JSON em vez de JSONB para compat com SQLite em CI/local. Em prod
    # (Postgres) o tipo JSON do SQLAlchemy mapeia pra JSONB nativo.
    module_roles: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = [
    "ROLES_VALIDOS",
    "ROLE_ADMIN",
    "ROLE_LEITOR",
    "ROLE_LEVEL",
    "ROLE_OPERADOR",
    "User",
]
