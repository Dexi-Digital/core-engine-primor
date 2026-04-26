"""Service para operacoes de auth: login, CRUD de usuarios, audit."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.core.security import hash_password, verify_password
from app.modules.auth.models import User

_AUDIT_RESOURCE = "auth.user"


async def _record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_id: int | None,
    actor: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Grava em audit_log -- segue padrao dos outros services."""
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource=_AUDIT_RESOURCE,
            resource_id=str(resource_id) if resource_id is not None else None,
            metadata_json=json.dumps(metadata, default=str)
            if metadata
            else None,
        )
    )
    await db.commit()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


# --- queries ----------------------------------------------------------------


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    return (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (
        await db.execute(
            select(User).where(User.email == _normalize_email(email))
        )
    ).scalar_one_or_none()


async def list_users(
    db: AsyncSession,
    *,
    only_active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[User]:
    stmt = select(User).order_by(User.id.asc())
    if only_active is True:
        stmt = stmt.where(User.is_active.is_(True))
    elif only_active is False:
        stmt = stmt.where(User.is_active.is_(False))
    stmt = stmt.limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().all())


# --- mutations --------------------------------------------------------------


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    nome: str,
    password: str,
    role: str,
    module_roles: dict | None = None,
    is_active: bool = True,
    actor: str,
) -> User:
    """Cria usuario. `email` ja deve vir validado pelo schema."""
    user = User(
        email=_normalize_email(email),
        nome=nome,
        password_hash=hash_password(password),
        role=role,
        module_roles=module_roles,
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await _record_audit(
        db,
        action="create",
        resource_id=user.id,
        actor=actor,
        metadata={
            "email": user.email,
            "role": user.role,
            "module_roles": user.module_roles,
        },
    )
    return user


async def update_user(
    db: AsyncSession,
    user: User,
    *,
    nome: str | None = None,
    password: str | None = None,
    role: str | None = None,
    module_roles: dict | None = None,
    is_active: bool | None = None,
    # `module_roles_set` distingue "nao tocar" (None) de "limpar" ({}).
    # Com Pydantic, dict vazio chega como `{}` e dict ausente como None.
    # Como passamos None=skip, para limpar use `{}`. Documentamos em
    # UserUpdate; aqui so refletimos.
    actor: str,
) -> User:
    """Update parcial. Audit so registra o que mudou de fato."""
    changed: dict[str, dict[str, Any]] = {}

    if nome is not None and nome != user.nome:
        changed["nome"] = {"from": user.nome, "to": nome}
        user.nome = nome
    if password is not None:
        # Nao logamos hash em audit -- so o evento.
        changed["password"] = {"from": "***", "to": "***"}
        user.password_hash = hash_password(password)
    if role is not None and role != user.role:
        changed["role"] = {"from": user.role, "to": role}
        user.role = role
    if module_roles is not None and module_roles != (user.module_roles or {}):
        changed["module_roles"] = {
            "from": user.module_roles,
            "to": module_roles or None,
        }
        user.module_roles = module_roles or None
    if is_active is not None and is_active != user.is_active:
        changed["is_active"] = {"from": user.is_active, "to": is_active}
        user.is_active = is_active

    if not changed:
        # No-op -- nao polui audit_log.
        return user

    await db.commit()
    await db.refresh(user)
    await _record_audit(
        db,
        action="update",
        resource_id=user.id,
        actor=actor,
        metadata={"changed": changed},
    )
    return user


async def delete_user(
    db: AsyncSession,
    user: User,
    *,
    actor: str,
) -> None:
    """Hard delete. Snapshot vai pro audit antes do delete fisico."""
    snapshot = {
        "email": user.email,
        "role": user.role,
        "module_roles": user.module_roles,
        "is_active": user.is_active,
    }
    user_id = user.id
    await db.delete(user)
    await db.commit()
    await _record_audit(
        db,
        action="delete",
        resource_id=user_id,
        actor=actor,
        metadata=snapshot,
    )


# --- auth -------------------------------------------------------------------


_DUMMY_BCRYPT_HASH: str | None = None


def _dummy_hash() -> str:
    """Hash valido de bcrypt para timing-attack mitigation no login.

    Lazy-init para nao gastar 80ms no import do modulo. O conteudo
    nao precisa ser conhecido (e nem deveria) -- so precisa ser uma
    hash bcrypt valida que nao bate com nada real.
    """
    global _DUMMY_BCRYPT_HASH
    if _DUMMY_BCRYPT_HASH is None:
        _DUMMY_BCRYPT_HASH = hash_password("__never_a_real_password__")
    return _DUMMY_BCRYPT_HASH


async def authenticate(
    db: AsyncSession, *, email: str, password: str
) -> User | None:
    """Retorna user ativo se senha bater, None caso contrario."""
    user = await get_user_by_email(db, email)
    if user is None:
        # Verificamos uma hash dummy mesmo assim para nao revelar via
        # timing se o email existe ou nao. `verify_password` em entrada
        # invalida custa ~80ms (bcrypt), mesma ordem da branch valida.
        verify_password(password, _dummy_hash())
        return None
    if not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def touch_last_login(db: AsyncSession, user: User) -> None:
    user.last_login_at = datetime.now(UTC)
    await db.commit()


__all__ = [
    "authenticate",
    "create_user",
    "delete_user",
    "get_user_by_email",
    "get_user_by_id",
    "list_users",
    "touch_last_login",
    "update_user",
]
