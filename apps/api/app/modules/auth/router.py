"""Endpoints de auth: login, refresh, me, CRUD de usuarios."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.modules.auth import service as auth_service
from app.modules.auth.dependencies import get_current_user, require_admin
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    LoginRequest,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserRead,
    UserUpdate,
)

router = APIRouter()


def _build_token_pair(user: User) -> TokenPair:
    settings = get_settings()
    sub = str(user.id)
    return TokenPair(
        access_token=create_access_token(sub, extra={"email": user.email}),
        refresh_token=create_refresh_token(sub),
        expires_in=settings.access_token_expire_minutes * 60,
        refresh_expires_in=settings.refresh_token_expire_days * 24 * 60 * 60,
    )


# --- login / refresh / me ---------------------------------------------------


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    user = await auth_service.authenticate(
        db, email=payload.email, password=payload.password
    )
    if user is None:
        # Mensagem deliberadamente generica para nao distinguir
        # email-invalido de senha-errada.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
        )
    await auth_service.touch_last_login(db, user)
    return _build_token_pair(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token invalido ou expirado",
    )
    try:
        decoded = decode_token(payload.refresh_token)
    except JWTError as e:
        raise creds_exc from e

    if decoded.get("type") != TOKEN_TYPE_REFRESH:
        raise creds_exc

    sub = decoded.get("sub")
    if not sub:
        raise creds_exc
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as e:
        raise creds_exc from e

    user = await auth_service.get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise creds_exc
    return _build_token_pair(user)


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


# --- CRUD de usuarios (admin-only) ------------------------------------------


@router.get("/users", response_model=list[UserRead])
async def list_users_endpoint(
    only_active: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[User]:
    return await auth_service.list_users(
        db, only_active=only_active, limit=limit, offset=offset
    )


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user_endpoint(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    existing = await auth_service.get_user_by_email(db, payload.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email ja cadastrado",
        )
    return await auth_service.create_user(
        db,
        email=payload.email,
        nome=payload.nome,
        password=payload.password,
        role=payload.role,
        module_roles=payload.module_roles,
        is_active=payload.is_active,
        actor=actor.email,
    )


@router.get("/users/{user_id}", response_model=UserRead)
async def get_user_endpoint(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> User:
    user = await auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario nao encontrado")
    return user


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user_endpoint(
    user_id: int,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    user = await auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario nao encontrado")
    # Anti-lockout: admin nao pode se auto-rebaixar (role) nem se
    # auto-desativar (is_active=False). Sem isso, o unico admin do
    # sistema poderia se trancar fora -- recovery exigiria intervencao
    # direta no DB ou ADMIN_EMAIL diferente no env (idempotente).
    if user.id == actor.id:
        if payload.role is not None and payload.role != actor.role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nao e possivel alterar o proprio role (use outro admin)",
            )
        if payload.is_active is not None and payload.is_active != actor.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nao e possivel alterar o proprio is_active (use outro admin)",
            )
    return await auth_service.update_user(
        db,
        user,
        nome=payload.nome,
        password=payload.password,
        role=payload.role,
        module_roles=payload.module_roles,
        is_active=payload.is_active,
        actor=actor.email,
    )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_endpoint(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> None:
    user = await auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario nao encontrado")
    if user.id == actor.id:
        # Evita o admin se auto-deletar (loop in foot).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nao e possivel deletar a propria conta",
        )
    await auth_service.delete_user(db, user, actor=actor.email)
