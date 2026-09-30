"""Envio de e-mail via Microsoft 365 (Graph sendMail).

Substitui o Resend. O e-mail e canal adicional dos alertas: o client
nao tem mock que finge enviar, e sem configuracao `build_mail_client`
devolve None.
"""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations.msgraph_mail.client import (
    GraphMailClient,
    GraphMailError,
    GraphMailPermissaoNegada,
    build_mail_client,
    mail_configurado,
)
from app.modules.notificacoes.alertas import despachar_alerta
from app.modules.notificacoes.models import Notificacao
from tests.fixtures.graph_mail.mock import SENDER, mock_graph_mailer


def _client(handler) -> GraphMailClient:
    return GraphMailClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        sender=SENDER,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_send_mail_monta_payload_do_graph() -> None:
    captured: list[httpx.Request] = []
    mailer = mock_graph_mailer(captured)
    await mailer.send_mail(
        to=["a@primor.example", " b@primor.example "],
        subject="Assunto",
        html="<p>oi</p>",
    )
    await mailer.aclose()

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert str(req.url) == f"https://graph.microsoft.com/v1.0/users/{SENDER}/sendMail"
    assert req.headers["Authorization"] == "Bearer tok-test"
    body = json.loads(req.read())
    assert body == {
        "message": {
            "subject": "Assunto",
            "body": {"contentType": "HTML", "content": "<p>oi</p>"},
            "toRecipients": [
                {"emailAddress": {"address": "a@primor.example"}},
                {"emailAddress": {"address": "b@primor.example"}},
            ],
        },
        "saveToSentItems": True,
    }


@pytest.mark.asyncio
async def test_token_e_reaproveitado_entre_envios() -> None:
    tokens = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            tokens["n"] += 1
            form = request.read().decode()
            assert "grant_type=client_credentials" in form
            assert "graph.microsoft.com%2F.default" in form
            return httpx.Response(200, json={"access_token": "tk", "expires_in": 3600})
        return httpx.Response(202)

    mailer = _client(handler)
    await mailer.send_mail(to=["a@b.com"], subject="1", html="x")
    await mailer.send_mail(to=["a@b.com"], subject="2", html="x")
    await mailer.aclose()
    assert tokens["n"] == 1


@pytest.mark.asyncio
async def test_403_vira_erro_de_permissao_explicito() -> None:
    mailer = mock_graph_mailer([], status=403)
    with pytest.raises(GraphMailPermissaoNegada) as exc:
        await mailer.send_mail(to=["a@b.com"], subject="s", html="h")
    await mailer.aclose()
    msg = str(exc.value)
    assert "403" in msg
    assert "Mail.Send" in msg
    assert SENDER in msg


@pytest.mark.asyncio
async def test_outros_erros_viram_graph_mail_error() -> None:
    mailer = mock_graph_mailer([], status=400)
    with pytest.raises(GraphMailError) as exc:
        await mailer.send_mail(to=["a@b.com"], subject="s", html="h")
    await mailer.aclose()
    assert not isinstance(exc.value, GraphMailPermissaoNegada)
    assert "400" in str(exc.value)


@pytest.mark.asyncio
async def test_falha_no_token_vira_graph_mail_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    mailer = _client(handler)
    with pytest.raises(GraphMailError):
        await mailer.send_mail(to=["a@b.com"], subject="s", html="h")
    assert await mailer.health_check() is False
    await mailer.aclose()


@pytest.mark.asyncio
async def test_sem_destinatario_levanta_value_error() -> None:
    mailer = mock_graph_mailer([])
    with pytest.raises(ValueError):
        await mailer.send_mail(to=[" "], subject="s", html="h")
    await mailer.aclose()


def test_build_mail_client_exige_graph_e_caixa_remetente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for nome in ("MS_GRAPH_TENANT_ID", "MS_GRAPH_CLIENT_ID", "MS_GRAPH_CLIENT_SECRET"):
        monkeypatch.setenv(nome, "x")
    monkeypatch.setenv("MAIL_SENDER", "")
    get_settings.cache_clear()
    assert mail_configurado(get_settings()) is False
    assert build_mail_client(get_settings()) is None

    monkeypatch.setenv("MAIL_SENDER", SENDER)
    get_settings.cache_clear()
    assert mail_configurado(get_settings()) is True
    client = build_mail_client(get_settings())
    assert isinstance(client, GraphMailClient)
    assert client.sender == SENDER
    get_settings.cache_clear()


# --- despachar_alerta (caminho unico dos alertas) --------------------------


async def _despachar(db: AsyncSession, mailer) -> object:
    return await despachar_alerta(
        db,
        recipients=["a@primor.example", "b@primor.example"],
        categoria="dp",
        titulo="t",
        corpo="c",
        link="/x",
        chave_idempotencia="teste:1",
        email_subject="s",
        email_html="<p>h</p>",
        mailer=mailer,
    )


@pytest.mark.asyncio
async def test_despachar_sem_mailer_so_cria_notificacoes(db_session: AsyncSession) -> None:
    r = await _despachar(db_session, None)
    assert r.email_status == "nao_configurado"
    assert r.notificacoes == 2
    notifs = (await db_session.execute(select(Notificacao))).scalars().all()
    assert len(notifs) == 2


@pytest.mark.asyncio
async def test_despachar_erro_de_rede_no_email_nao_perde_notificacao(
    db_session: AsyncSession,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "tk", "expires_in": 3600})
        raise httpx.ConnectError("sem rede")

    mailer = _client(handler)
    r = await _despachar(db_session, mailer)
    await mailer.aclose()
    assert r.email_status == "falhou"
    assert "sem rede" in (r.email_error or "")
    notifs = (await db_session.execute(select(Notificacao))).scalars().all()
    assert len(notifs) == 2
