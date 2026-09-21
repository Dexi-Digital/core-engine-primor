"""Tests for the boletins por email dispatcher (D.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.resend.client import ResendClient, ResendError
from app.modules.licitacoes.boletins import (
    create_saved_query,
    dispatch_boletins,
    render_digest_html,
)
from app.modules.licitacoes.models import BoletimLog, Licitacao


async def _seed_licitacoes(db: AsyncSession, rows: list[dict]) -> list[Licitacao]:
    objs = [Licitacao(**r) for r in rows]
    db.add_all(objs)
    await db.commit()
    for obj in objs:
        await db.refresh(obj)
    return objs


def _row(
    *,
    external_id: str,
    objeto: str,
    uf: str = "SP",
    modalidade: str = "Pregão Eletrônico",
    valor: Decimal | None = Decimal("100000.00"),
) -> dict:
    return {
        "external_id": external_id,
        "source": "pncp",
        "objeto_compra": objeto,
        "modalidade_nome": modalidade,
        "uf_sigla": uf,
        "orgao_razao_social": "Prefeitura Teste",
        "orgao_cnpj": "12345678000100",
        "valor_total_estimado": valor,
        "data_publicacao_pncp": datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
    }


@pytest.mark.asyncio
async def test_render_digest_html_contains_object_and_dashboard_link(
    db_session: AsyncSession,
) -> None:
    seeded = await _seed_licitacoes(
        db_session,
        [_row(external_id="ext-1", objeto="Pavimentação asfáltica BR-040", uf="MG")],
    )
    query = await create_saved_query(
        db_session,
        nome="Pavimentacao MG",
        user_email="lorrayne@dexidigital.com.br",
        recipients=["lorrayne@dexidigital.com.br"],
        uf="MG",
        search="pavimentacao",
    )

    html = render_digest_html(query, seeded, public_base_url="https://motorcentral.example")

    assert "Pavimentação asfáltica BR-040" in html
    assert "Pavimentacao MG" in html  # nome da query no header
    assert "UF=MG" in html and 'busca="pavimentacao"' in html  # filtros visiveis
    assert "https://motorcentral.example/licitacoes?uf=MG&amp;search=pavimentacao" in html
    assert "R$ 100.000,00" in html  # valor formatado pt-BR


@pytest.mark.asyncio
async def test_render_digest_html_url_encodes_search_with_spaces_and_accents(
    db_session: AsyncSession,
) -> None:
    seeded = await _seed_licitacoes(
        db_session,
        [_row(external_id="ext-1", objeto="Obra teste", uf="SP")],
    )
    query = await create_saved_query(
        db_session,
        nome="teste url",
        user_email="x@y.com",
        recipients=["x@y.com"],
        uf="SP",
        search="pavimentação asfáltica",
    )

    html = render_digest_html(query, seeded, public_base_url="https://motorcentral.example")

    # Must contain URL-encoded search, not raw spaces/accents.
    # `quote()` encodes space as %20 and ã as %C3%A3, ç as %C3%A7.
    assert "search=pavimenta%C3%A7%C3%A3o%20asf%C3%A1ltica" in html
    # And the raw broken form must NOT appear.
    assert "search=pavimentação asfáltica" not in html


@pytest.mark.asyncio
async def test_dispatch_sends_email_and_advances_cursor(
    db_session: AsyncSession,
) -> None:
    seeded = await _seed_licitacoes(
        db_session,
        [
            _row(external_id="ext-1", objeto="Compra de cimento"),
            _row(external_id="ext-2", objeto="Compra de areia"),
        ],
    )
    query = await create_saved_query(
        db_session,
        nome="SP cimento",
        user_email="tester@primor.com",
        recipients=["tester@primor.com", "cc@primor.com"],
        uf="SP",
        search="compra",
    )

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "url": str(request.url),
                "body": request.content.decode() if request.content else "",
            }
        )
        return httpx.Response(200, json={"id": "resend-msg-abc", "to": ["tester@primor.com"]})

    summary = await dispatch_boletins(
        db_session,
        public_base_url="https://motorcentral.example",
    )

    assert summary.total_queries == 1
    assert summary.sent == 1
    assert summary.skipped_empty == 0
    assert summary.failed == 0
    assert summary.results[0].licitacoes_count == 2
    assert summary.results[0].last_licitacao_id == max(s.id for s in seeded)

    # Os dois destinatarios receberam notificacao propria.
    from app.modules.notificacoes import service as _notif
    for _email in ("tester@primor.com", "cc@primor.com"):
        assert len(await _notif.listar(db_session, destinatario=_email)) == 1

    # BoletimLog row persisted with cursor = highest licitacao id.
    log = (
        await db_session.execute(
            BoletimLog.__table__.select().where(BoletimLog.saved_query_id == query.id)
        )
    ).first()
    assert log is not None
    assert log.status == "sent"
    assert log.last_licitacao_id == max(s.id for s in seeded)

    # A second dispatch with NO new rows should skip (cursor is at top).
    summary2 = await dispatch_boletins(
        db_session, public_base_url="https://motorcentral.example"
    )
    assert summary2.skipped_empty == 1
    assert summary2.sent == 0

    # Add a new row AFTER the cursor -> should fire.
    new_rows = await _seed_licitacoes(
        db_session,
        [_row(external_id="ext-3", objeto="Compra de brita")],
    )
    summary3 = await dispatch_boletins(
        db_session, public_base_url="https://motorcentral.example"
    )
    assert summary3.sent == 1
    assert summary3.results[0].licitacoes_count == 1
    assert summary3.results[0].last_licitacao_id == new_rows[0].id


@pytest.mark.asyncio
async def test_dispatch_isolates_failures_and_does_not_advance_cursor(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falha ao notificar NAO pode avancar o cursor.

    Se avancasse, a proxima rodada acharia "nada novo" e aquelas
    licitacoes nunca seriam avisadas a ninguem -- perda silenciosa, que
    num captador de oportunidade significa prazo perdido.
    """
    await _seed_licitacoes(db_session, [_row(external_id="ext-1", objeto="Obra teste")])
    await create_saved_query(
        db_session,
        nome="SP tudo",
        user_email="tester@primor.com",
        recipients=["tester@primor.com"],
        uf="SP",
    )

    async def explode(*a, **k):
        raise RuntimeError("banco de notificacoes indisponivel")

    monkeypatch.setattr(
        "app.modules.licitacoes.boletins.criar_notificacao", explode
    )

    summary = await dispatch_boletins(db_session)

    assert summary.failed == 1
    assert summary.sent == 0
    assert summary.results[0].status == "failed"
    assert "indisponivel" in (summary.results[0].error_message or "")

    # Cursor segue None: a proxima rodada tenta as MESMAS linhas.
    log = (
        await db_session.execute(
            BoletimLog.__table__.select().order_by(BoletimLog.sent_at.desc())
        )
    ).first()
    assert log is not None
    assert log.status == "failed"
    assert log.last_licitacao_id is None


@pytest.mark.asyncio
async def test_resend_raises_on_empty_recipients() -> None:
    resend = ResendClient(api_key="re_test")
    with pytest.raises(ValueError):
        await resend.send_email(to=[], subject="x", html="<p>hi</p>", from_="x@y.com")
    await resend.aclose()


@pytest.mark.asyncio
async def test_resend_reraises_non_recoverable_as_resend_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "invalid"})

    resend = ResendClient(
        api_key="re_test",
        client=httpx.AsyncClient(
            base_url="https://mock.resend",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer re_test"},
        ),
    )
    with pytest.raises(ResendError):
        await resend.send_email(to=["x@y.com"], subject="x", html="<p>h</p>", from_="x@y.com")
    await resend.aclose()


# --- Router CRUD ---


@pytest.mark.asyncio
async def test_crud_saved_queries_roundtrip(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/licitacoes/boletins/saved-queries",
        json={
            "nome": "SP pavimentacao",
            "user_email": "tester@primor.com",
            "recipients": ["tester@primor.com"],
            "uf": "SP",
            "search": "pavimenta",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    saved_id = created["id"]
    assert created["nome"] == "SP pavimentacao"
    assert created["uf"] == "SP"

    resp = await api_client.get("/api/v1/licitacoes/boletins/saved-queries")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == saved_id

    resp = await api_client.delete(
        f"/api/v1/licitacoes/boletins/saved-queries/{saved_id}", headers=auth_headers
    )
    assert resp.status_code == 204

    resp = await api_client.get("/api/v1/licitacoes/boletins/saved-queries")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_dispatch_funciona_sem_chave_de_email(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """Sem RESEND_API_KEY o despacho tem de funcionar igual.

    Este teste afirmava o CONTRARIO (503 sem a chave) ate 21/09/2026.
    Com o boletim virando notificacao na plataforma, exigir chave de
    email deixaria o despacho indisponivel por causa de uma
    dependencia que nao e mais usada.
    """
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        resp = await api_client.post(
            "/api/v1/licitacoes/boletins/dispatch", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_saved_query_rejects_empty_recipients(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/licitacoes/boletins/saved-queries",
        json={
            "nome": "x",
            "user_email": "a@b.com",
            "recipients": [],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


# --- Boletim vira notificacao na plataforma (21/09/2026) --------------------
#
# O cliente pediu que o boletim parasse de mandar email e virasse
# notificacao dentro da plataforma. O que NAO muda: o cursor
# (`last_licitacao_id`) e o isolamento de falha por query -- essa parte
# ja estava certa e os testes acima continuam valendo para ela.


@pytest.mark.asyncio
async def test_dispatch_cria_notificacao_para_cada_destinatario(
    db_session: AsyncSession,
) -> None:
    """Uma notificacao POR pessoa, nao uma compartilhada.

    Cada destinatario le e marca como lida a sua -- caixa de entrada
    compartilhada faria a leitura de um sumir o aviso do outro.
    """
    from app.modules.notificacoes import service as notif_svc

    query = await create_saved_query(
        db_session,
        nome="Obras SP",
        user_email="tester@primor.com",
        recipients=["tester@primor.com", "cc@primor.com"],
        uf="SP",
    )
    await _seed_licitacoes(db_session, [_row(external_id="n-1", objeto="Ponte")])

    summary = await dispatch_boletins(db_session)

    assert summary.sent == 1
    for email in ("tester@primor.com", "cc@primor.com"):
        caixa = await notif_svc.listar(db_session, destinatario=email)
        assert len(caixa) == 1, f"{email} nao recebeu"
        assert caixa[0].categoria == "licitacoes"
        assert query.nome in caixa[0].titulo


@pytest.mark.asyncio
async def test_dispatch_nao_chama_resend(db_session: AsyncSession) -> None:
    """A garantia central do pedido: nenhum email sai daqui.

    `dispatch_boletins` nem recebe mais um ResendClient -- passar um
    seria TypeError, o que torna impossivel religar o email sem mexer
    na assinatura de proposito.
    """
    import inspect

    from app.modules.licitacoes import boletins

    params = inspect.signature(boletins.dispatch_boletins).parameters
    assert "resend" not in params


@pytest.mark.asyncio
async def test_notificacao_do_boletim_nao_duplica(db_session: AsyncSession) -> None:
    """O beat roda 3x/dia. Sem chave, o sino encheria de copias."""
    from app.modules.notificacoes import service as notif_svc

    await create_saved_query(
        db_session,
        nome="Obras SP",
        user_email="tester@primor.com",
        recipients=["tester@primor.com"],
        uf="SP",
    )
    await _seed_licitacoes(db_session, [_row(external_id="n-2", objeto="Asfalto")])

    await dispatch_boletins(db_session)
    await dispatch_boletins(db_session)  # cursor ja no topo -> skipped_empty

    caixa = await notif_svc.listar(db_session, destinatario="tester@primor.com")
    assert len(caixa) == 1


@pytest.mark.asyncio
async def test_notificacao_leva_para_a_licitacao(db_session: AsyncSession) -> None:
    """Notificacao sem link obriga a pessoa a procurar o que mudou."""
    from app.modules.notificacoes import service as notif_svc

    await create_saved_query(
        db_session,
        nome="Obras SP",
        user_email="tester@primor.com",
        recipients=["tester@primor.com"],
        uf="SP",
    )
    await _seed_licitacoes(db_session, [_row(external_id="n-3", objeto="Drenagem")])

    await dispatch_boletins(db_session)
    caixa = await notif_svc.listar(db_session, destinatario="tester@primor.com")
    assert caixa[0].link and caixa[0].link.startswith("/licitacoes")


@pytest.mark.asyncio
async def test_corpo_da_notificacao_nao_leva_html(db_session: AsyncSession) -> None:
    """O objeto vem do PNCP -- texto de terceiro.

    O digest de email e HTML porque cliente de email pede HTML. O painel
    renderiza o corpo como texto, e injetar HTML de terceiro ali seria
    abrir XSS numa tela autenticada.
    """
    from app.modules.notificacoes import service as notif_svc

    await create_saved_query(
        db_session,
        nome="Obras SP",
        user_email="tester@primor.com",
        recipients=["tester@primor.com"],
        uf="SP",
    )
    await _seed_licitacoes(
        db_session,
        [_row(external_id="n-4", objeto="<script>alert(1)</script> Obra")],
    )

    await dispatch_boletins(db_session)
    caixa = await notif_svc.listar(db_session, destinatario="tester@primor.com")
    assert "<script" not in caixa[0].corpo
    assert "<" not in caixa[0].corpo
