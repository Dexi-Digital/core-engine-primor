"""FastAPI dependencies para auth + RBAC.

Padrao:

    @router.post("/...")
    async def endpoint(
        user: User = Depends(require_role("operador", modulo="rh")),
        db: AsyncSession = Depends(get_db),
    ): ...

`get_current_user` so resolve o user do JWT (sem checagem de role).
`require_role(...)` faz a checagem de RBAC e retorna o user ou 403.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.auth.permissions import has_at_least

# `auto_error=False` para tratar manualmente -- queremos 401 (nao 403)
# quando falta o header, e mensagens consistentes em portugues.
_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_current_user(
    token: str | None = Depends(_oauth2),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Valida JWT do header `Authorization: Bearer ...` e retorna o User.

    Sempre 401 em qualquer falha (token ausente, invalido, expirado,
    user removido, user inativo). Nao distinguimos para nao vazar info
    util pra atacante.
    """
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais invalidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise creds_exc
    try:
        payload = decode_token(token)
    except JWTError as e:
        raise creds_exc from e

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        # Refresh token mandado como access -- bloqueamos.
        raise creds_exc

    sub = payload.get("sub")
    if not sub:
        raise creds_exc
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as e:
        raise creds_exc from e

    user = await auth_service.get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise creds_exc
    return user


def require_role(required: str, *, modulo: str | None = None):
    """Factory de dependency: garante role >= `required` no `modulo`.

    Ex.: `require_role("admin")` exige admin global.
    `require_role("operador", modulo="rh")` exige operador no rh
    (ou admin global, que herda).
    """

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if not has_at_least(user, modulo=modulo, required=required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Acesso negado: requer role '{required}'"
                    + (f" no modulo '{modulo}'" if modulo else " global")
                ),
            )
        return user

    return _dep


require_admin = require_role("admin")


__all__ = [
    "get_current_user",
    "require_admin",
    "require_role",
]
