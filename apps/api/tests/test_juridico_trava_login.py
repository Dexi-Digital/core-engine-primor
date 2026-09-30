"""Trava de login do EasyJur ENTRE execucoes.

O EasyJur bloqueia a conta apos 5 senhas erradas seguidas. O contador
do client vive na instancia, e cada execucao do pull cria uma nova --
entao sem esta trava o beat diario e cada clique em "Sincronizar agora"
queimariam uma tentativa ate bloquear a conta de quem usa o sistema.

Usa o client REAL com transporte mock, contando as chamadas a
`login.php`: e isso que importa, nao o que o nosso codigo acha que fez.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.integrations.easyjur.client import EasyjurClient
from app.modules.juridico import service as svc
from app.modules.juridico.models import SyncLog

FIX = Path(__file__).parent / "fixtures" / "easyjur"

_RECUSA = {
    "status": 400,
    "erros": {
        "codigo": 7,
        "mensagem": "Você digitou a senha incorreta 1 vez(es) para este login.",
        "tentativas_restantes": {"email": 4, "ip": 4},
    },
}


class EasyjurFalso:
    """Servidor EasyJur de mentira. `aceita` decide o login."""

    def __init__(self, *, aceita: bool) -> None:
        self.aceita = aceita
        self.logins = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/api/login.php"):
            self.logins += 1
            if self.aceita:
                return httpx.Response(200, json={"status": 200})
            return httpx.Response(200, json=_RECUSA)
        if path.endswith("ajax_processos_lista.php"):
            if b"page=1&" in request.content:
                return httpx.Response(200, text=(FIX / "processos_pagina.html").read_text())
            return httpx.Response(
                200, text="<table><tbody></tbody></table><h3>453  Registros Encontrados</h3>"
            )
        if path.endswith("export_andamentos.php"):
            return httpx.Response(200, content=(FIX / "andamentos_export.csv").read_bytes())
        return httpx.Response(200, text="<html></html>")

    def client(self, *, email: str = "x@y.com", password: str = "errada") -> EasyjurClient:
        return EasyjurClient(
            email=email,
            password=password,
            client=httpx.AsyncClient(
                base_url="https://app.easyjur.com",
                transport=httpx.MockTransport(self.handler),
            ),
        )


async def _rodar(
    db: AsyncSession, servidor: EasyjurFalso, *, password: str = "errada", source: str = "beat"
):
    client = servidor.client(password=password)
    try:
        return await svc.executar_carga(
            db,
            client,
            source=source,
            impressao=svc.impressao_credencial("x@y.com", password),
        )
    finally:
        await client.aclose()


async def _eventos(db: AsyncSession) -> list[AuditLog]:
    return list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.resource == svc.AUDIT_RESOURCE_LOGIN)
                .order_by(AuditLog.id)
            )
        ).scalars().all()
    )


def test_impressao_nao_guarda_a_senha() -> None:
    a = svc.impressao_credencial("x@y.com", "segredo123")
    assert a is not None and "segredo123" not in a
    assert a == svc.impressao_credencial("X@Y.com ", "segredo123")
    assert a != svc.impressao_credencial("x@y.com", "segredo124")
    assert svc.impressao_credencial("x@y.com", "") is None


async def test_recusa_do_easyjur_fica_gravada(db_session: AsyncSession) -> None:
    servidor = EasyjurFalso(aceita=False)
    with pytest.raises(svc.LoginEasyjurBloqueado, match="trocar a credencial"):
        await _rodar(db_session, servidor)
    assert servidor.logins == 1
    eventos = await _eventos(db_session)
    assert [e.action for e in eventos] == [svc.ACAO_LOGIN_RECUSADO]
    assert eventos[0].resource_id == svc.impressao_credencial("x@y.com", "errada")
    meta = json.loads(eventos[0].metadata_json or "{}")
    assert meta["tentativas_restantes"] == 4
    assert "errada" not in (eventos[0].metadata_json or "")


async def test_proxima_execucao_nao_tenta_logar(db_session: AsyncSession) -> None:
    """O defeito que esta trava fecha: sem ela, cada madrugada gastava
    uma tentativa ate o EasyJur bloquear a conta."""
    servidor = EasyjurFalso(aceita=False)
    with pytest.raises(svc.LoginEasyjurBloqueado):
        await _rodar(db_session, servidor)
    for _ in range(3):
        with pytest.raises(svc.LoginEasyjurBloqueado, match="nova tentativa só após"):
            await _rodar(db_session, servidor)
    assert servidor.logins == 1  # so a primeira tocou no login.php


async def test_credencial_trocada_tenta_de_novo(db_session: AsyncSession) -> None:
    servidor = EasyjurFalso(aceita=False)
    with pytest.raises(svc.LoginEasyjurBloqueado):
        await _rodar(db_session, servidor)

    servidor.aceita = True
    r = await _rodar(db_session, servidor, password="certa")
    assert servidor.logins == 2
    assert r.processos == 3
    # Login aceito fecha a trava na trilha.
    assert [e.action for e in await _eventos(db_session)] == [
        svc.ACAO_LOGIN_RECUSADO,
        svc.ACAO_LOGIN_OK,
    ]


async def test_sucesso_limpa_a_trava_e_voltar_a_senha_antiga_tenta_uma_vez(
    db_session: AsyncSession,
) -> None:
    servidor = EasyjurFalso(aceita=False)
    with pytest.raises(svc.LoginEasyjurBloqueado):
        await _rodar(db_session, servidor)
    servidor.aceita = True
    await _rodar(db_session, servidor, password="certa")
    assert await svc.login_bloqueado(
        db_session, svc.impressao_credencial("x@y.com", "errada")
    ) is None


async def test_sessao_expirada_nao_trava_o_login(db_session: AsyncSession) -> None:
    """Sessao que cai no meio da carga tambem e `EasyjurAuthError`, mas
    nao gastou tentativa: nao pode travar a credencial."""
    from app.integrations.easyjur.client import EasyjurAuthError

    servidor = EasyjurFalso(aceita=True)
    original = servidor.handler

    def expira(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("ajax_processos_lista.php"):
            return httpx.Response(200, text='<form><input type="password"></form>')
        return original(request)

    servidor.handler = expira  # type: ignore[method-assign]
    with pytest.raises(EasyjurAuthError, match="expirada"):
        await _rodar(db_session, servidor, password="certa")
    assert await _eventos(db_session) == []
    servidor.handler = original  # type: ignore[method-assign]
    await _rodar(db_session, servidor, password="certa")
    assert servidor.logins == 2


async def test_login_ok_sem_trava_nao_grava_ruido(db_session: AsyncSession) -> None:
    servidor = EasyjurFalso(aceita=True)
    await _rodar(db_session, servidor, password="certa")
    await _rodar(db_session, servidor, password="certa")
    assert await _eventos(db_session) == []


async def test_liberacao_do_admin_autoriza_uma_tentativa(db_session: AsyncSession) -> None:
    servidor = EasyjurFalso(aceita=False)
    with pytest.raises(svc.LoginEasyjurBloqueado):
        await _rodar(db_session, servidor)
    assert await svc.liberar_login(db_session, actor="admin@primor") is True

    with pytest.raises(svc.LoginEasyjurBloqueado):
        await _rodar(db_session, servidor)  # tenta uma vez...
    with pytest.raises(svc.LoginEasyjurBloqueado):
        await _rodar(db_session, servidor)  # ...e trava de novo
    assert servidor.logins == 2
    acoes = [(e.action, e.actor) for e in await _eventos(db_session)]
    assert (svc.ACAO_LOGIN_LIBERADO, "admin@primor") in acoes


async def test_liberar_sem_trava_nao_grava(db_session: AsyncSession) -> None:
    assert await svc.liberar_login(db_session, actor="admin@primor") is False
    assert await _eventos(db_session) == []


# --- API: o clique mostra o motivo e nao enfileira ----------------------------


class _DispatcherFalso:
    def __init__(self) -> None:
        self.chamadas: list = []

    def send_task(self, *a, **k):
        self.chamadas.append((a, k))


@pytest.fixture
def credencial_errada(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("EASYJUR_EMAIL", "x@y.com")
    monkeypatch.setenv("EASYJUR_PASSWORD", "errada")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _travar(db: AsyncSession) -> None:
    with pytest.raises(svc.LoginEasyjurBloqueado):
        await _rodar(db, EasyjurFalso(aceita=False))


async def test_sync_manual_travado_da_423_sem_enfileirar(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch, credencial_errada,
) -> None:
    await _travar(db_session)
    disp = _DispatcherFalso()
    monkeypatch.setattr(svc, "get_celery_dispatcher", lambda: disp)

    r = await api_client.post("/api/v1/juridico/sync", headers=auth_headers)
    assert r.status_code == 423
    assert "trocar a credencial" in r.json()["detail"]
    assert disp.chamadas == []
    log = (
        await db_session.execute(select(SyncLog).where(SyncLog.source == "manual"))
    ).scalar_one()
    assert log.status == "erro"
    assert "trocar a credencial" in (log.erro or "")


async def test_resumo_mostra_a_trava(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession,
    credencial_errada,
) -> None:
    await _travar(db_session)
    r = await api_client.get("/api/v1/juridico/resumo", headers=auth_headers)
    assert r.status_code == 200
    login = r.json()["login_easyjur"]
    assert login["bloqueado"] is True
    assert "trocar a credencial" in login["mensagem"]


async def test_endpoint_liberar_destrava_e_audita_o_admin(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch, credencial_errada,
) -> None:
    await _travar(db_session)
    r = await api_client.post("/api/v1/juridico/login/liberar", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["liberado"] is True

    disp = _DispatcherFalso()
    monkeypatch.setattr(svc, "get_celery_dispatcher", lambda: disp)
    r = await api_client.post("/api/v1/juridico/sync", headers=auth_headers)
    assert r.status_code == 202
    assert len(disp.chamadas) == 1
    liberacao = (await _eventos(db_session))[-1]
    assert liberacao.action == svc.ACAO_LOGIN_LIBERADO
    assert liberacao.actor != "system:worker"


async def test_endpoint_liberar_exige_autenticacao(api_client: AsyncClient) -> None:
    r = await api_client.post("/api/v1/juridico/login/liberar")
    assert r.status_code == 401
