"""Tests for the boletins por email dispatcher (D.3)."""
from __future__ import annotations

from datetime import datetime, timezone
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
from app.modules.licitacoes.models import BoletimLog, Licitacao, SavedQuery


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
        "data_publicacao_pncp": datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
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

    html = render_digest_html(
        query, seeded, public_base_url="https://motorcentral.example"
    )

    assert "Pavimentação asfáltica BR-040" in html
    assert "Pavimentacao MG" in html  # nome da query no header
    assert "UF=MG" in html and 'busca="pavimentacao"' in html  # filtros visiveis
    assert "https://motorcentral.example/licitacoes?uf=MG&amp;search=pavimentacao" in html
    assert "R$ 100.000,00" in html  # valor formatado pt-BR


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
        return httpx.Response(
            200, json={"id": "resend-msg-abc", "to": ["tester@primor.com"]}
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://mock.resend",
        transport=transport,
        headers={"Authorization": "Bearer re_test"},
    )
    resend = ResendClient(api_key="re_test", client=http)

    summary = await dispatch_boletins(
        db_session,
        resend,
        public_base_url="https://motorcentral.example",
        from_email="boletins@motorcentral.example",
    )

    assert summary.total_queries == 1
    assert summary.sent == 1
    assert summary.skipped_empty == 0
    assert summary.failed == 0
    assert summary.results[0].resend_message_id == "resend-msg-abc"
    assert summary.results[0].licitacoes_count == 2
    assert summary.results[0].last_licitacao_id == max(s.id for s in seeded)

    # Resend was called exactly once with both recipients.
    assert len(calls) == 1
    assert '"tester@primor.com"' in calls[0]["body"]
    assert '"cc@primor.com"' in calls[0]["body"]

    # BoletimLog row persisted with cursor = highest licitacao id.
    log = (
        await db_session.execute(
            BoletimLog.__table__.select().where(
                BoletimLog.saved_query_id == query.id
            )
        )
    ).first()
    assert log is not None
    assert log.status == "sent"
    assert log.last_licitacao_id == max(s.id for s in seeded)

    # A second dispatch with NO new rows should skip (cursor is at top).
    summary2 = await dispatch_boletins(
        db_session, resend, public_base_url="https://motorcentral.example"
    )
    assert summary2.skipped_empty == 1
    assert summary2.sent == 0
    # Resend should NOT have been called again.
    assert len(calls) == 1

    # Add a new row AFTER the cursor -> should fire.
    new_rows = await _seed_licitacoes(
        db_session,
        [_row(external_id="ext-3", objeto="Compra de brita")],
    )
    summary3 = await dispatch_boletins(
        db_session, resend, public_base_url="https://motorcentral.example"
    )
    assert summary3.sent == 1
    assert summary3.results[0].licitacoes_count == 1
    assert summary3.results[0].last_licitacao_id == new_rows[0].id
    assert len(calls) == 2

    await resend.aclose()


@pytest.mark.asyncio
async def test_dispatch_isolates_failures_and_does_not_advance_cursor(
    db_session: AsyncSession,
) -> None:
    await _seed_licitacoes(
        db_session, [_row(external_id="ext-1", objeto="Obra teste")]
    )
    await create_saved_query(
        db_session,
        nome="SP tudo",
        user_email="tester@primor.com",
        recipients=["tester@primor.com"],
        uf="SP",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        # Simulate Resend rejecting payload (bad-from etc). 400 is NOT retried
        # and should bubble as ResendError.
        return httpx.Response(400, json={"message": "from not verified"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        base_url="https://mock.resend",
        transport=transport,
        headers={"Authorization": "Bearer re_test"},
    )
    resend = ResendClient(api_key="re_test", client=http)

    summary = await dispatch_boletins(db_session, resend)

    assert summary.failed == 1
    assert summary.sent == 0
    assert summary.results[0].status == "failed"
    assert summary.results[0].error_message is not None
    assert "from not verified" in summary.results[0].error_message

    # After failure, cursor must remain None so next run retries the same rows.
    log = (
        await db_session.execute(
            BoletimLog.__table__.select().order_by(BoletimLog.sent_at.desc())
        )
    ).first()
    assert log is not None
    assert log.status == "failed"
    assert log.last_licitacao_id is None

    await resend.aclose()


@pytest.mark.asyncio
async def test_resend_raises_on_empty_recipients() -> None:
    resend = ResendClient(api_key="re_test")
    with pytest.raises(ValueError):
        await resend.send_email(
            to=[], subject="x", html="<p>hi</p>", from_="x@y.com"
        )
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
        await resend.send_email(
            to=["x@y.com"], subject="x", html="<p>h</p>", from_="x@y.com"
        )
    await resend.aclose()


# --- Router CRUD ---


@pytest.mark.asyncio
async def test_crud_saved_queries_roundtrip(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/licitacoes/boletins/saved-queries",
        json={
            "nome": "SP pavimentacao",
            "user_email": "tester@primor.com",
            "recipients": ["tester@primor.com"],
            "uf": "SP",
            "search": "pavimenta",
        },
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
        f"/api/v1/licitacoes/boletins/saved-queries/{saved_id}"
    )
    assert resp.status_code == 204

    resp = await api_client.get("/api/v1/licitacoes/boletins/saved-queries")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_dispatch_endpoint_returns_503_without_api_key(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate an unconfigured deploy: no RESEND_API_KEY in the environment.
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()

    resp = await api_client.post("/api/v1/licitacoes/boletins/dispatch")
    assert resp.status_code == 503
    assert "RESEND_API_KEY" in resp.json()["detail"]
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_saved_query_rejects_empty_recipients(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/licitacoes/boletins/saved-queries",
        json={
            "nome": "x",
            "user_email": "a@b.com",
            "recipients": [],
        },
    )
    assert resp.status_code == 422
