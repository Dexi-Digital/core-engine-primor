"""Hooks de startup do modulo auth.

- `warn_dev_secret`: em `environment=production` o startup ABORTA se
  `secret_key` ainda for o default (qualquer um com acesso ao codigo
  forjaria tokens). Em dev/staging so loga warning visivel.
- `ensure_admin_seed`: cria o primeiro admin se `ADMIN_EMAIL` /
  `ADMIN_PASSWORD` vierem no env e ainda nao existir usuario com esse
  email. Idempotente -- pode rodar a cada boot sem efeitos colaterais.
"""
from __future__ import annotations

import structlog

from app.core.config import Settings
from app.core.db import SessionLocal
from app.modules.auth import service as auth_service
from app.modules.auth.models import ROLE_ADMIN

logger = structlog.get_logger(__name__)

_DEV_SECRET = "change-me-in-production"
_PROD_ENVIRONMENTS = {"production", "prod"}


class InsecureProductionSecretError(RuntimeError):
    """Levantado quando `environment=production` mas `SECRET_KEY` ainda
    e o default. Bloqueia o startup -- nao da pra subir API com tokens
    forjaveis pelo mundo."""


def warn_dev_secret(settings: Settings) -> None:
    if settings.secret_key != _DEV_SECRET:
        return
    if settings.environment.lower() in _PROD_ENVIRONMENTS:
        raise InsecureProductionSecretError(
            "ENVIRONMENT=production exige SECRET_KEY customizado. "
            "Gere um valor com `openssl rand -hex 32` e defina no .env "
            "antes de subir a API."
        )
    logger.warning(
        "JWT secret_key is still the dev default; tokens are forgeable. "
        "Set SECRET_KEY in .env before deploying.",
        environment=settings.environment,
    )


async def ensure_admin_seed(settings: Settings) -> None:
    """Cria o admin inicial se ADMIN_EMAIL/ADMIN_PASSWORD vierem no env.

    Loga sem segredos. Sem credenciais configuradas, no-op (uso valido
    em CI / desenvolvimento puro -- testes criam admin em fixture).
    """
    if not settings.admin_email or not settings.admin_password:
        return

    async with SessionLocal() as db:
        existing = await auth_service.get_user_by_email(
            db, settings.admin_email
        )
        if existing is not None:
            logger.info(
                "admin seed: usuario ja existe, pulando",
                admin_email=existing.email,
            )
            return
        await auth_service.create_user(
            db,
            email=settings.admin_email,
            nome=settings.admin_nome,
            password=settings.admin_password,
            role=ROLE_ADMIN,
            module_roles=None,
            is_active=True,
            actor="system:seed",
        )
        logger.info(
            "admin seed: usuario admin criado",
            admin_email=settings.admin_email,
        )


__all__ = [
    "InsecureProductionSecretError",
    "ensure_admin_seed",
    "warn_dev_secret",
]
