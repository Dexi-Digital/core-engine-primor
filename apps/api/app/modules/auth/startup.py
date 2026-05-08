"""Hooks de startup do modulo auth.

- `warn_dev_secret`: em `environment=production` o startup ABORTA se
  `secret_key` ainda for o default (qualquer um com acesso ao codigo
  forjaria tokens). Em dev/staging so loga warning visivel.
- `guard_local_storage_in_prod`: simetrico ao secret guard -- em
  producao, `STORAGE_BACKEND=local` com `EDITAIS_STORAGE_PATH` num
  diretorio efemero (`/tmp/...`) aborta o startup. `/tmp` e wiped em
  restart do container, e anexos de licitacao/ASO/XML fiscal desapareceriam.
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
# Prefixos de paths efemeros que nao podem ser usados como storage
# persistente em prod. `/tmp` e o classico (`tmpfs` em muitos
# containers), `/var/tmp` e wiped menos frequentemente mas ainda
# nao e seguro, e `/dev/shm` e memoria pura.
_EPHEMERAL_PATH_PREFIXES: tuple[str, ...] = ("/tmp", "/var/tmp", "/dev/shm")


class InsecureProductionSecretError(RuntimeError):
    """Levantado quando `environment=production` mas `SECRET_KEY` ainda
    e o default. Bloqueia o startup -- nao da pra subir API com tokens
    forjaveis pelo mundo."""


class InsecureProductionStorageError(RuntimeError):
    """Levantado quando `environment=production` + `STORAGE_BACKEND=local`
    + `EDITAIS_STORAGE_PATH` esta em diretorio efemero (`/tmp` etc).
    Bloqueia o startup -- anexos sumiriam em qualquer restart do container."""


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


def guard_local_storage_in_prod(settings: Settings) -> None:
    """Aborta se prod + local storage + path efemero.

    Em dev/staging e no-op -- so valida a combinacao perigosa em
    producao. Se o backend e `onedrive` (ou qualquer outro nao-local)
    o path do disco e irrelevante; so checamos o local.
    """
    if settings.environment.lower() not in _PROD_ENVIRONMENTS:
        return
    backend = (settings.storage_backend or "local").lower()
    if backend != "local":
        return
    path = settings.editais_storage_path or ""
    if not any(path.startswith(prefix) for prefix in _EPHEMERAL_PATH_PREFIXES):
        return
    raise InsecureProductionStorageError(
        "ENVIRONMENT=production com STORAGE_BACKEND=local exige "
        "EDITAIS_STORAGE_PATH em volume persistente. "
        f"Path atual ({path!r}) comeca com um prefixo efemero "
        f"({_EPHEMERAL_PATH_PREFIXES}) -- anexos desapareceriam no "
        "primeiro restart do container. Configure um volume montado "
        "(ex: /data/motor-central/editais) ou troque para "
        "STORAGE_BACKEND=onedrive. Ver docs/deployment-option-a.md."
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
    "InsecureProductionStorageError",
    "ensure_admin_seed",
    "guard_local_storage_in_prod",
    "warn_dev_secret",
]
