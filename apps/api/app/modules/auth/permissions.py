"""RBAC helpers: resolver papel efetivo do usuario para um modulo."""
from __future__ import annotations

from app.modules.auth.models import (
    ROLE_LEITOR,
    ROLE_LEVEL,
    ROLES_VALIDOS,
    User,
)

# Modulos conhecidos -- usados para validar `module_roles` na criacao
# do usuario. Nao restringimos acesso aqui (qualquer router pode chamar
# `effective_role`); a lista existe so para detectar typos cedo.
MODULOS = frozenset(
    {
        "rh",
        "frota",
        "fiscal",
        "licitacoes",
        "financeiro",
        "observability",
    }
)


def effective_role(user: User, modulo: str | None) -> str:
    """Retorna o papel efetivo do usuario no modulo dado.

    Se `modulo` for None ou nao houver override em `module_roles`, usa
    o `role` global. `module_roles` invalido ou com role desconhecida
    cai no global (defensivo -- nao queremos crashar acesso por causa
    de um JSON corrompido em DB).
    """
    if modulo and user.module_roles:
        override = user.module_roles.get(modulo)
        if isinstance(override, str) and override in ROLES_VALIDOS:
            return override
    return user.role


def has_at_least(user: User, *, modulo: str | None, required: str) -> bool:
    """`user` tem ao menos `required` no `modulo`?

    Ex.: `has_at_least(user, modulo="rh", required="operador")` retorna
    True para admin global ou operador no rh, False para leitor.
    """
    if required not in ROLES_VALIDOS:
        # Fail closed -- se o caller passou role invalida, nega.
        return False
    role = effective_role(user, modulo)
    return ROLE_LEVEL.get(role, 0) >= ROLE_LEVEL[required]


def validate_module_roles(module_roles: dict | None) -> dict | None:
    """Valida o dict de overrides; retorna None se vazio.

    Mantem so chaves que sao modulos conhecidos e valores que sao roles
    validas. Falha early (`ValueError`) em entrada visivelmente quebrada
    em vez de silenciosamente descartar -- o admin que ta criando o
    usuario precisa saber que digitou errado.
    """
    if not module_roles:
        return None
    cleaned: dict[str, str] = {}
    for k, v in module_roles.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError(
                f"module_roles entry must be (str, str); got ({type(k).__name__}, {type(v).__name__})"
            )
        if k not in MODULOS:
            raise ValueError(
                f"modulo desconhecido em module_roles: {k!r} (validos: {sorted(MODULOS)})"
            )
        if v not in ROLES_VALIDOS:
            raise ValueError(
                f"role invalida em module_roles[{k!r}]: {v!r} (validas: {sorted(ROLES_VALIDOS)})"
            )
        cleaned[k] = v
    return cleaned or None


__all__ = [
    "MODULOS",
    "ROLE_LEITOR",
    "effective_role",
    "has_at_least",
    "validate_module_roles",
]
