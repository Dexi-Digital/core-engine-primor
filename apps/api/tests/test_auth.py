"""Auth: login, refresh, RBAC, CRUD de usuarios, audit_log."""
from __future__ import annotations

import json
import time

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.core.security import (
    create_access_token,
    create_refresh_token,
)
from app.modules.auth import service as auth_service
from app.modules.auth.models import (
    ROLE_ADMIN,
    ROLE_LEITOR,
    ROLE_OPERADOR,
    User,
)
from app.modules.auth.permissions import effective_role, has_at_least

# --- helpers ----------------------------------------------------------------


async def _create_user(
    db: AsyncSession,
    *,
    email: str = "admin@primor.com",
    password: str = "secret-pw-1234",
    role: str = ROLE_ADMIN,
    module_roles: dict | None = None,
    is_active: bool = True,
) -> User:
    return await auth_service.create_user(
        db,
        email=email,
        nome="Admin Teste",
        password=password,
        role=role,
        module_roles=module_roles,
        is_active=is_active,
        actor="system:test",
    )


async def _login_token(
    api_client: AsyncClient, email: str, password: str
) -> str:
    resp = await api_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _bearer(api_client: AsyncClient, token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- login ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_ok(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="alice@primor.com", password="hunter22aa")
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@primor.com", "password": "hunter22aa"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert "refresh_token" in body
    # Access tem expires_in em segundos.
    assert body["expires_in"] >= 60


@pytest.mark.asyncio
async def test_login_email_normaliza(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Email salvo lowercase mas login com case misto deve funcionar."""
    await _create_user(db_session, email="bob@primor.com", password="hunter22bb")
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "  Bob@PRIMOR.com  ", "password": "hunter22bb"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_login_senha_errada(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="carol@primor.com", password="hunter22cc")
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "carol@primor.com", "password": "errada"},
    )
    assert resp.status_code == 401
    # Mensagem generica -- nao distingue email-existe-mas-senha-errada
    # de email-nao-existe (timing-attack mitigation).
    assert "Email ou senha" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_login_email_inexistente_retorna_mesma_mensagem(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "fantasma@primor.com", "password": "qualquercoisa"},
    )
    assert resp.status_code == 401
    assert "Email ou senha" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_login_user_inativo_falha(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(
        db_session, email="banido@primor.com", password="hunter22dd", is_active=False
    )
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "banido@primor.com", "password": "hunter22dd"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_atualiza_last_login_at(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session, email="eve@primor.com", password="hunter22ee")
    assert user.last_login_at is None
    await _login_token(api_client, "eve@primor.com", "hunter22ee")
    refreshed = await auth_service.get_user_by_id(db_session, user.id)
    assert refreshed is not None
    assert refreshed.last_login_at is not None


# --- /me e dependencia get_current_user -------------------------------------


@pytest.mark.asyncio
async def test_me_retorna_usuario_logado(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="alice@primor.com", password="hunter22aa")
    token = await _login_token(api_client, "alice@primor.com", "hunter22aa")
    resp = await api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == "alice@primor.com"
    assert body["role"] == ROLE_ADMIN
    # password_hash NAO sai na resposta.
    assert "password" not in body
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_me_sem_token_401(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_token_invalido_401(api_client: AsyncClient) -> None:
    resp = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_access_com_refresh_token_falha(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Mandar refresh token onde se espera access deve dar 401."""
    user = await _create_user(db_session, email="alice@primor.com", password="hunter22aa")
    refresh = create_refresh_token(str(user.id))
    resp = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_access_token_expirado_401(
    api_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token com exp no passado -> jose decode raises -> 401."""
    user = await _create_user(db_session, email="alice@primor.com", password="hunter22aa")

    # Patch expiry para -1 minuto antes de criar.
    from app.core import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "access_token_expire_minutes", -1)
    expired = create_access_token(str(user.id))
    resp = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_user_deletado_invalida_token(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session, email="alice@primor.com", password="hunter22aa")
    token = await _login_token(api_client, "alice@primor.com", "hunter22aa")
    # Deleta o usuario diretamente -- token continua "valido" criptograficamente
    # mas o user nao existe mais -> 401.
    await db_session.delete(user)
    await db_session.commit()
    resp = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


# --- /auth/refresh ----------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_emite_novos_tokens(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="alice@primor.com", password="hunter22aa")
    login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@primor.com", "password": "hunter22aa"},
    )
    refresh_token = login.json()["refresh_token"]
    # Pequena espera para garantir mudanca no `iat`.
    time.sleep(1.05)
    resp = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert resp.status_code == 200, resp.text
    new_tokens = resp.json()
    assert new_tokens["access_token"] != login.json()["access_token"]


@pytest.mark.asyncio
async def test_refresh_com_access_token_falha(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await _create_user(db_session, email="alice@primor.com", password="hunter22aa")
    access = create_access_token(str(user.id))
    resp = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": access}
    )
    assert resp.status_code == 401


# --- RBAC -------------------------------------------------------------------


def test_effective_role_global_default() -> None:
    user = User(
        email="x@y.com",
        nome="X",
        password_hash="x",
        role=ROLE_OPERADOR,
        module_roles=None,
    )
    assert effective_role(user, "rh") == ROLE_OPERADOR
    assert effective_role(user, None) == ROLE_OPERADOR


def test_effective_role_module_override() -> None:
    user = User(
        email="x@y.com",
        nome="X",
        password_hash="x",
        role=ROLE_LEITOR,
        module_roles={"rh": ROLE_ADMIN, "frota": ROLE_OPERADOR},
    )
    assert effective_role(user, "rh") == ROLE_ADMIN
    assert effective_role(user, "frota") == ROLE_OPERADOR
    # Modulo sem override cai no global.
    assert effective_role(user, "fiscal") == ROLE_LEITOR


def test_effective_role_invalid_override_cai_no_global() -> None:
    """Defensivo: se DB tiver JSON corrompido, nao crasha o request."""
    user = User(
        email="x@y.com",
        nome="X",
        password_hash="x",
        role=ROLE_OPERADOR,
        module_roles={"rh": "deus_supremo"},
    )
    assert effective_role(user, "rh") == ROLE_OPERADOR


def test_has_at_least_admin_satisfaz_tudo() -> None:
    user = User(email="x@y.com", nome="X", password_hash="x", role=ROLE_ADMIN, module_roles=None)
    assert has_at_least(user, modulo=None, required=ROLE_ADMIN)
    assert has_at_least(user, modulo="rh", required=ROLE_OPERADOR)
    assert has_at_least(user, modulo="frota", required=ROLE_LEITOR)


def test_has_at_least_leitor_so_satisfaz_leitor() -> None:
    user = User(email="x@y.com", nome="X", password_hash="x", role=ROLE_LEITOR, module_roles=None)
    assert has_at_least(user, modulo=None, required=ROLE_LEITOR)
    assert not has_at_least(user, modulo=None, required=ROLE_OPERADOR)
    assert not has_at_least(user, modulo=None, required=ROLE_ADMIN)


# --- /auth/users (admin-only) -----------------------------------------------


@pytest.mark.asyncio
async def test_list_users_exige_admin(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="leitor@primor.com", password="hunter22ll", role=ROLE_LEITOR)
    token = await _login_token(api_client, "leitor@primor.com", "hunter22ll")
    resp = await api_client.get(
        "/api/v1/auth/users", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_list_users_admin_ok(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    await _create_user(db_session, email="leitor@primor.com", password="hunter22ll", role=ROLE_LEITOR)
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.get(
        "/api/v1/auth/users", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    emails = {u["email"] for u in resp.json()}
    assert {"admin@primor.com", "leitor@primor.com"} <= emails


@pytest.mark.asyncio
async def test_create_user_admin_ok(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "novo@primor.com",
            "nome": "Novo Operador",
            "password": "hunter22nn",
            "role": "operador",
            "module_roles": {"rh": "admin"},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "novo@primor.com"
    assert body["role"] == "operador"
    assert body["module_roles"] == {"rh": "admin"}


@pytest.mark.asyncio
async def test_create_user_email_duplicado_409(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "admin@primor.com",
            "nome": "Outro",
            "password": "hunter22xx",
            "role": "leitor",
        },
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_user_role_invalida_422(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "novo2@primor.com",
            "nome": "Novo",
            "password": "hunter22nn",
            "role": "deus_supremo",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_user_module_role_invalida_422(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "novo3@primor.com",
            "nome": "Novo",
            "password": "hunter22nn",
            "role": "leitor",
            "module_roles": {"modulo_inexistente": "admin"},
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_user_grava_audit_so_do_que_mudou(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    target = await _create_user(
        db_session, email="alvo@primor.com", password="hunter22aa", role=ROLE_LEITOR
    )
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    # PATCH so muda nome -- nao re-emite role no audit.
    resp = await api_client.patch(
        f"/api/v1/auth/users/{target.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"nome": "Alvo Atualizado"},
    )
    assert resp.status_code == 200
    audits = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.resource == "auth.user")
            .where(AuditLog.resource_id == str(target.id))
            .where(AuditLog.action == "update")
        )
    ).scalars().all()
    assert len(audits) == 1
    metadata = json.loads(audits[0].metadata_json or "{}")
    assert "changed" in metadata
    assert "nome" in metadata["changed"]
    # role/password nao mudaram -> nao aparecem em changed.
    assert "role" not in metadata["changed"]
    # actor e o email do admin que fez o request, nao "system".
    assert audits[0].actor == admin.email


@pytest.mark.asyncio
async def test_update_user_no_op_nao_polui_audit(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    target = await _create_user(
        db_session, email="alvo@primor.com", password="hunter22aa", role=ROLE_LEITOR
    )
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.patch(
        f"/api/v1/auth/users/{target.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"nome": "Admin Teste"},  # mesmo nome
    )
    assert resp.status_code == 200
    audits = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.resource == "auth.user")
            .where(AuditLog.action == "update")
            .where(AuditLog.resource_id == str(target.id))
        )
    ).scalars().all()
    assert audits == []


@pytest.mark.asyncio
async def test_delete_user_admin_ok_e_audit(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    target = await _create_user(db_session, email="alvo@primor.com", password="hunter22aa", role=ROLE_LEITOR)
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.delete(
        f"/api/v1/auth/users/{target.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
    # User foi deletado.
    assert await auth_service.get_user_by_id(db_session, target.id) is None
    # Audit registra delete + snapshot.
    audits = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.resource == "auth.user")
            .where(AuditLog.action == "delete")
            .where(AuditLog.resource_id == str(target.id))
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].actor == admin.email
    metadata = json.loads(audits[0].metadata_json or "{}")
    assert metadata["email"] == "alvo@primor.com"


@pytest.mark.asyncio
async def test_delete_self_proibido(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.delete(
        f"/api/v1/auth/users/{admin.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_module_roles_dict_vazio_limpa_overrides(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Regressao Devin Review: PATCH `{"module_roles": {}}` deve limpar
    overrides; antes da fix, validate_module_roles colapsava {} em None
    e o service nao distinguia "nao tocar" de "limpar"."""
    await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    target = await _create_user(
        db_session,
        email="alvo@primor.com",
        password="hunter22aa",
        role=ROLE_LEITOR,
        module_roles={"rh": ROLE_ADMIN},
    )
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.patch(
        f"/api/v1/auth/users/{target.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"module_roles": {}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["module_roles"] is None
    # Audit registra o clear (changed.module_roles.to == None).
    audits = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.resource == "auth.user")
            .where(AuditLog.resource_id == str(target.id))
            .where(AuditLog.action == "update")
        )
    ).scalars().all()
    assert len(audits) == 1
    metadata = json.loads(audits[0].metadata_json or "{}")
    assert metadata["changed"]["module_roles"]["to"] is None


@pytest.mark.asyncio
async def test_login_user_inativo_paga_custo_bcrypt(
    api_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regressao Devin Review: `authenticate()` com user inativo deve
    chamar verify_password (mitigacao de timing-attack). Antes da fix,
    a branch retornava em ~0ms e ficava distinguivel via timing."""
    await _create_user(
        db_session,
        email="banido@primor.com",
        password="hunter22dd",
        is_active=False,
    )

    calls: list[str] = []
    from app.modules.auth import service as svc

    real_verify = svc.verify_password

    def _spy(plain: str, hashed: str) -> bool:
        calls.append(hashed)
        return real_verify(plain, hashed)

    monkeypatch.setattr(svc, "verify_password", _spy)

    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "banido@primor.com", "password": "hunter22dd"},
    )
    assert resp.status_code == 401
    # Houve EXATAMENTE uma chamada de verify_password no caminho
    # is_active=False (com a hash real do user).
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_create_user_grava_audit_com_actor_email(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Regressao: actor em audit_log e o email do admin logado, nao 'system'.

    Isso e o item-chave que destrava o requisito de LGPD/AGENTS.md
    'toda mutacao registra QUEM fez'. Com o auth real plumbed, o actor
    sai do JWT e via Depends entra no service. O placeholder antigo
    `actor='system'` so deve aparecer pra mutacoes de origem cron/seed.
    """
    admin = await _create_user(db_session, email="admin@primor.com", password="hunter22zz")
    token = await _login_token(api_client, "admin@primor.com", "hunter22zz")
    resp = await api_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "novo4@primor.com",
            "nome": "Novo",
            "password": "hunter22nn",
            "role": "leitor",
        },
    )
    assert resp.status_code == 201
    new_id = resp.json()["id"]
    audits = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.resource == "auth.user")
            .where(AuditLog.action == "create")
            .where(AuditLog.resource_id == str(new_id))
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].actor == admin.email
