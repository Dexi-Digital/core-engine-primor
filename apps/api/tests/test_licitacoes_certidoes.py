"""Tests for D.6 certidoes/atestados domain."""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.certidoes import (
    JANELAS_ALERTA,
    compute_status,
    create_certidao,
    dispatch_expiration_alerts,
    janela_for_certidao,
    list_certidoes,
    render_alerta_html,
)
from app.modules.licitacoes.models import CertidaoAlertaLog, CertidaoEmpresa
from app.modules.notificacoes.models import Notificacao
from tests.fixtures.graph_mail.mock import SENDER, mock_graph_mailer

# --- pure helpers (no DB) ----------------------------------------------------


def test_compute_status_buckets() -> None:
    today = date(2026, 4, 25)
    # vigente: validade > 30 dias
    assert compute_status(date(2026, 6, 1), today=today) == "vigente"
    # vencendo: <= 30 dias
    assert compute_status(date(2026, 5, 25), today=today) == "vencendo"
    # vencendo: hoje (0d ainda vigente, mas dentro da janela)
    assert compute_status(date(2026, 4, 25), today=today) == "vencendo"
    # vencido: ontem
    assert compute_status(date(2026, 4, 24), today=today) == "vencido"
    # sem validade (atestados perpetuos)
    assert compute_status(None, today=today) == "sem_validade"


def test_janela_for_certidao_picks_smallest_remaining() -> None:
    today = date(2026, 4, 25)
    # 35 dias -> ainda fora da janela 30 -> None
    assert janela_for_certidao(today + timedelta(days=35), today=today) is None
    # exatamente 30 -> janela 30
    assert janela_for_certidao(today + timedelta(days=30), today=today) == 30
    # 12 dias -> janela mais urgente ainda nao acionada e a 15 (15 >= 12)
    assert janela_for_certidao(today + timedelta(days=12), today=today) == 15
    # 5 dias -> 7
    assert janela_for_certidao(today + timedelta(days=5), today=today) == 7
    # 0 dias (hoje) -> 0
    assert janela_for_certidao(today, today=today) == 0
    # ja venceu ha 1 dia -> None (alertas pos-vencimento ficam fora)
    assert janela_for_certidao(today - timedelta(days=1), today=today) is None
    # sem validade -> None
    assert janela_for_certidao(None, today=today) is None


def test_render_alerta_html_includes_essentials() -> None:
    cert = CertidaoEmpresa(
        id=1,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        numero="ABC-123",
        emissao=date(2026, 1, 1),
        validade=date(2026, 5, 5),
        orgao_emissor="Receita Federal",
    )
    html = render_alerta_html(
        cert,
        janela=15,
        public_base_url="https://motorcentral.example",
        dias_restantes=10,
    )
    assert "Certidao Negativa de Debitos Federais" in html  # label expandida
    assert "44229813000123" in html
    assert "ABC-123" in html
    assert "05/05/2026" in html
    assert "Vence em 10 dia(s)" in html
    assert "https://motorcentral.example/licitacoes/certidoes" in html, (
        "deve linkar para o dashboard"
    )


def test_render_alerta_html_today_uses_vencida_label() -> None:
    cert = CertidaoEmpresa(
        id=2,
        empresa_cnpj="44229813000123",
        tipo="FGTS",
        validade=date.today(),
    )
    html = render_alerta_html(cert, janela=0, public_base_url="https://x.example", dias_restantes=0)
    assert "VENCIDA hoje" in html


def test_render_alerta_html_uses_actual_days_not_janela_threshold() -> None:
    """Regressao: o email tem que mostrar dias REAIS ate o vencimento, nao
    o threshold da janela (30/15/7). Cert com 12 dias cai na janela 15 mas
    o usuario precisa ler '12 dia(s)', nao '15 dias' -- senao perde prazo."""
    cert = CertidaoEmpresa(
        id=10,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        validade=date(2026, 5, 7),
    )
    html = render_alerta_html(
        cert,
        janela=15,
        public_base_url="https://x.example",
        dias_restantes=12,
    )
    assert "12 dia(s)" in html
    # E nao deve mostrar o numero da janela como se fosse a contagem real.
    assert "Vence em 15 dias" not in html


# --- service layer ------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_certidao(db_session: AsyncSession) -> None:
    today = date(2026, 4, 25)
    row = await create_certidao(
        db_session,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        numero="123",
        validade=today + timedelta(days=10),
    )
    assert row.id is not None

    rows = await list_certidoes(db_session)
    assert len(rows) == 1
    assert rows[0].numero == "123"


@pytest.mark.asyncio
async def test_list_certidoes_filters_by_status(db_session: AsyncSession) -> None:
    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="CND_FEDERAL",
        validade=today + timedelta(days=60),  # vigente
    )
    await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="FGTS",
        validade=today + timedelta(days=10),  # vencendo
    )
    await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="CNDT",
        validade=today - timedelta(days=2),  # vencida
    )
    await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="ATESTADO_CAT",
        validade=None,  # sem_validade
    )

    vigentes = await list_certidoes(db_session, status="vigente", today=today)
    vencendo = await list_certidoes(db_session, status="vencendo", today=today)
    vencidas = await list_certidoes(db_session, status="vencido", today=today)
    sem_val = await list_certidoes(db_session, status="sem_validade", today=today)
    assert {r.tipo for r in vigentes} == {"CND_FEDERAL"}
    assert {r.tipo for r in vencendo} == {"FGTS"}
    assert {r.tipo for r in vencidas} == {"CNDT"}
    assert {r.tipo for r in sem_val} == {"ATESTADO_CAT"}


# --- dispatch: notificacao sempre + e-mail Graph opcional --------------------


async def _notificacoes(db: AsyncSession) -> list[Notificacao]:
    return list((await db.execute(select(Notificacao))).scalars().all())


@pytest.mark.asyncio
async def test_dispatch_sem_email_cria_notificacao_por_destinatario(
    db_session: AsyncSession,
) -> None:
    """Sem nenhuma config de e-mail o alerta vale: 1 notificacao por
    destinatario, log `sent` com email_status=nao_configurado."""
    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        validade=today + timedelta(days=10),  # janela 15d
    )

    summary = await dispatch_expiration_alerts(
        db_session,
        recipients=["lic@primor.example", "Dir@Primor.example"],
        today=today,
    )

    assert summary.sent == 1
    assert summary.failed == 0
    assert summary.results[0].email_status == "nao_configurado"
    notifs = await _notificacoes(db_session)
    assert sorted(n.destinatario for n in notifs) == [
        "dir@primor.example",
        "lic@primor.example",
    ]
    n = notifs[0]
    assert n.categoria == "certidoes"
    assert n.link == "/licitacoes/certidoes"
    # Dias REAIS (10), nao a janela (15).
    assert "vence em 10 dia" in n.titulo.lower()
    assert "vence em 15 dia" not in n.titulo.lower()
    log = (await db_session.execute(select(CertidaoAlertaLog))).scalar_one()
    assert log.status == "sent"
    assert log.email_status == "nao_configurado"


@pytest.mark.asyncio
async def test_dispatch_com_email_configurado_envia_pelo_graph(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        validade=today + timedelta(days=10),
    )

    captured: list[httpx.Request] = []
    mailer = mock_graph_mailer(captured)
    summary = await dispatch_expiration_alerts(
        db_session, recipients=["lic@primor.example"], mailer=mailer, today=today
    )
    await mailer.aclose()

    assert summary.sent == 1
    assert summary.results[0].email_status == "enviado"
    assert len(captured) == 1
    req = captured[0]
    assert req.url.path == f"/v1.0/users/{SENDER}/sendMail"
    body = json.loads(req.read())
    assert body["saveToSentItems"] is True
    assert body["message"]["toRecipients"] == [
        {"emailAddress": {"address": "lic@primor.example"}}
    ]
    assert "vence em 10 dia" in body["message"]["subject"].lower()
    assert len(await _notificacoes(db_session)) == 1


@pytest.mark.asyncio
async def test_dispatch_graph_403_mantem_notificacao_e_registra_falha(
    db_session: AsyncSession,
) -> None:
    """Permissao Mail.Send ainda nao concedida: o alerta conta como
    despachado (notificacao existe) e a falha do e-mail fica no log."""
    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="FGTS",
        validade=today + timedelta(days=5),
    )

    captured: list[httpx.Request] = []
    mailer = mock_graph_mailer(captured, status=403)
    summary = await dispatch_expiration_alerts(
        db_session, recipients=["x@y.com"], mailer=mailer, today=today
    )
    await mailer.aclose()

    assert summary.sent == 1
    assert summary.failed == 0
    assert summary.results[0].email_status == "falhou"
    assert "Mail.Send" in (summary.results[0].email_error or "")
    assert len(await _notificacoes(db_session)) == 1
    log = (await db_session.execute(select(CertidaoAlertaLog))).scalar_one()
    assert log.status == "sent"
    assert log.email_status == "falhou"
    assert "403" in (log.email_error or "")

    # Proximo cron nao reenvia: o alerta ja foi despachado.
    summary2 = await dispatch_expiration_alerts(
        db_session, recipients=["x@y.com"], today=today
    )
    assert summary2.skipped == 1
    assert len(await _notificacoes(db_session)) == 1


@pytest.mark.asyncio
async def test_dispatch_is_idempotent_for_same_window(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="FGTS",
        validade=today + timedelta(days=5),  # janela 7d
    )

    captured: list[httpx.Request] = []
    mailer = mock_graph_mailer(captured)

    summary1 = await dispatch_expiration_alerts(
        db_session, recipients=["x@y.com"], mailer=mailer, today=today
    )
    summary2 = await dispatch_expiration_alerts(
        db_session, recipients=["x@y.com"], mailer=mailer, today=today
    )
    await mailer.aclose()

    assert summary1.sent == 1
    assert summary2.sent == 0
    assert summary2.skipped == 1
    assert len(captured) == 1, "Email enviado uma so vez (idempotente)"
    assert len(await _notificacoes(db_session)) == 1


@pytest.mark.asyncio
async def test_dispatch_skips_when_no_validade(db_session: AsyncSession) -> None:
    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="ATESTADO_CAT",
        validade=None,
    )

    summary = await dispatch_expiration_alerts(
        db_session, recipients=["x@y.com"], today=today
    )
    assert summary.sent == 0
    assert summary.skipped == 1
    assert await _notificacoes(db_session) == []


@pytest.mark.asyncio
async def test_dispatch_retries_after_failure_without_unique_violation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressao: 1a tentativa falha (erro ao criar a notificacao) grava
    log status=failed; 2a tentativa deve atualizar in-place (UPDATE), nao
    tentar INSERT que violaria a UniqueConstraint(certidao_id, janela)."""
    import app.modules.licitacoes.certidoes as certidoes_mod

    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        validade=today + timedelta(days=10),  # janela 15d
    )

    real_despachar = certidoes_mod.despachar_alerta

    async def boom(*args, **kwargs):
        raise RuntimeError("banco indisponivel")

    monkeypatch.setattr(certidoes_mod, "despachar_alerta", boom)
    summary1 = await dispatch_expiration_alerts(
        db_session, recipients=["x@y.com"], today=today
    )
    assert summary1.failed == 1
    assert summary1.sent == 0
    logs = (await db_session.execute(CertidaoAlertaLog.__table__.select())).all()
    assert len(logs) == 1

    # Segunda rodada: ainda falha. Sem o fix, isto crasharia com IntegrityError.
    summary2 = await dispatch_expiration_alerts(
        db_session, recipients=["x@y.com"], today=today
    )
    assert summary2.failed == 1
    logs = (await db_session.execute(CertidaoAlertaLog.__table__.select())).all()
    assert len(logs) == 1, "log deve ter sido atualizado in-place, nao duplicado"

    # Terceira rodada: volta a funcionar. O log failed deve virar sent.
    monkeypatch.setattr(certidoes_mod, "despachar_alerta", real_despachar)
    summary3 = await dispatch_expiration_alerts(
        db_session, recipients=["x@y.com"], today=today
    )
    assert summary3.sent == 1
    assert summary3.failed == 0
    log = (await db_session.execute(select(CertidaoAlertaLog))).scalar_one()
    assert log.status == "sent"
    assert log.error_message is None


@pytest.mark.asyncio
async def test_update_certidao_can_clear_validade_to_null(
    db_session: AsyncSession,
) -> None:
    """Regressao: PUT com `validade=None` deve converter certidao em
    'sem_validade' (atestados perpetuos podem ter sido cadastrados com
    data de validade por engano)."""
    from app.modules.licitacoes.certidoes import update_certidao

    today = date(2026, 4, 25)
    row = await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="ATESTADO_CAT",
        validade=today + timedelta(days=30),
    )
    assert row.validade is not None

    updated = await update_certidao(db_session, row.id, validade=None)
    assert updated is not None
    assert updated.validade is None


@pytest.mark.asyncio
async def test_dispatch_skips_with_empty_recipients(
    db_session: AsyncSession,
) -> None:
    """Sem destinatarios -> early return, nem notificacao nem e-mail."""
    today = date(2026, 4, 25)
    await create_certidao(db_session, empresa_cnpj="X", tipo="FGTS", validade=today)
    captured: list[httpx.Request] = []
    mailer = mock_graph_mailer(captured)
    summary = await dispatch_expiration_alerts(
        db_session, recipients=[], mailer=mailer, today=today
    )
    await mailer.aclose()
    assert summary.total_certidoes == 0
    assert summary.sent == 0
    assert len(captured) == 0
    assert await _notificacoes(db_session) == []


# --- Router endpoints --------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_endpoints_full_lifecycle(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # Create. Diferente das funcoes puras (`compute_status` etc) que
    # aceitam `today=` injetado, este teste bate no router real, que
    # consulta `date.today()` ao calcular `status_atual`. Usar data
    # hardcoded aqui torna o teste sensivel a passagem do tempo --
    # `today + 10d` vira "vencido" assim que a data hardcoded passa
    # de 30d atras de hoje. Por isso ancoramos em `date.today()`.
    today = date.today()
    payload = {
        "empresa_cnpj": "44229813000123",
        "tipo": "CND_FEDERAL",
        "numero": "ABC",
        "validade": (today + timedelta(days=10)).isoformat(),
    }
    r = await api_client.post("/api/v1/licitacoes/certidoes", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    certidao_id = r.json()["id"]
    assert r.json()["status_atual"] in {"vigente", "vencendo"}

    # List
    r = await api_client.get("/api/v1/licitacoes/certidoes")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Get
    r = await api_client.get(f"/api/v1/licitacoes/certidoes/{certidao_id}")
    assert r.status_code == 200
    assert r.json()["numero"] == "ABC"

    # Update
    r = await api_client.put(
        f"/api/v1/licitacoes/certidoes/{certidao_id}",
        json={"numero": "ABC-2"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["numero"] == "ABC-2"

    # Delete
    r = await api_client.delete(f"/api/v1/licitacoes/certidoes/{certidao_id}", headers=auth_headers)
    assert r.status_code == 204
    r = await api_client.get(f"/api/v1/licitacoes/certidoes/{certidao_id}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_certidao_cleans_up_storage_anexo(
    db_session: AsyncSession,
) -> None:
    """Regressao: DELETE de uma certidao com `arquivo_path` tem que
    chamar `storage.delete(arquivo_path)` para nao vazar anexos no
    OneDrive/disco. Leak real observado antes do fix: DB row sumia
    mas o item ficava orfao no Graph.
    """
    from app.modules.licitacoes.certidoes import (
        create_certidao,
        delete_certidao,
    )

    class _RecordingStorage:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def save(self, **_kwargs):  # pragma: no cover - unused
            raise NotImplementedError

        async def read(self, path: str) -> bytes:  # pragma: no cover
            raise NotImplementedError

        async def delete(self, path: str) -> None:
            self.deleted.append(path)

    storage = _RecordingStorage()
    row = await create_certidao(
        db_session,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        validade=date.today() + timedelta(days=10),
        arquivo_path="ONEDRIVE_ITEM_ID_123",
        actor="test",
    )
    assert await delete_certidao(
        db_session, row.id, storage=storage, actor="test"
    )
    assert storage.deleted == ["ONEDRIVE_ITEM_ID_123"]


@pytest.mark.asyncio
async def test_delete_certidao_without_anexo_does_not_call_storage(
    db_session: AsyncSession,
) -> None:
    """Sem arquivo_path, nao chamamos storage.delete (evita noise
    em logs e eventual 404 na Graph). Tambem tolera storage=None
    para callers antigos que ainda nao foram migrados."""
    from app.modules.licitacoes.certidoes import (
        create_certidao,
        delete_certidao,
    )

    class _ExplodingStorage:
        async def save(self, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def read(self, path: str) -> bytes:  # pragma: no cover
            raise NotImplementedError

        async def delete(self, path: str) -> None:
            raise AssertionError(f"storage.delete nao deveria ter sido chamado (path={path})")

    row = await create_certidao(
        db_session,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        validade=date.today() + timedelta(days=10),
        arquivo_path=None,
        actor="test",
    )
    assert await delete_certidao(
        db_session, row.id, storage=_ExplodingStorage(), actor="test"
    )


@pytest.mark.asyncio
async def test_delete_certidao_storage_failure_is_best_effort(
    db_session: AsyncSession,
) -> None:
    """Falha no storage (Graph 5xx, disco cheio, etc) nao pode travar
    o DELETE -- a linha do DB ja foi removida. So loga warning."""
    from app.modules.licitacoes.certidoes import (
        create_certidao,
        delete_certidao,
    )

    class _FailingStorage:
        async def save(self, **_kwargs):  # pragma: no cover
            raise NotImplementedError

        async def read(self, path: str) -> bytes:  # pragma: no cover
            raise NotImplementedError

        async def delete(self, path: str) -> None:
            raise OSError("graph 503")

    row = await create_certidao(
        db_session,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        validade=date.today() + timedelta(days=10),
        arquivo_path="ONEDRIVE_ITEM_ID_999",
        actor="test",
    )
    # NAO deve levantar -- o cleanup e best-effort.
    assert await delete_certidao(
        db_session, row.id, storage=_FailingStorage(), actor="test"
    )


@pytest.mark.asyncio
async def test_dispatch_alerts_endpoint_sem_email_nao_da_503(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Sem e-mail configurado o endpoint responde 200 e cria notificacao
    (antes: 503 sem chave do provedor de e-mail)."""
    await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="FGTS",
        validade=date.today() + timedelta(days=5),
    )
    r = await api_client.post(
        "/api/v1/licitacoes/certidoes/dispatch-alerts",
        json={"recipients": ["x@y.com"]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] == 1
    assert body["results"][0]["email_status"] == "nao_configurado"
    assert "resend_message_id" not in body["results"][0]
    assert len(await _notificacoes(db_session)) == 1


def test_janelas_alerta_constant_is_descending() -> None:
    """Sanity: as janelas precisam estar em ordem decrescente para `min` no
    janela_for_certidao funcionar quando ha mais de 1 candidata."""
    assert list(JANELAS_ALERTA) == sorted(JANELAS_ALERTA, reverse=True)
