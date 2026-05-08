"""Tests for D.6 certidoes/atestados domain."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.resend.client import ResendClient
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


# --- dispatch (mock Resend via httpx.MockTransport) ---------------------------


def _mock_resend_client(captured: list[httpx.Request], message_id: str = "msg_xyz") -> ResendClient:
    """Build a ResendClient pointing at a mock transport (no real HTTP)."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": message_id})

    return ResendClient(
        api_key="re_test",
        client=httpx.AsyncClient(
            base_url="https://mock.resend",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer re_test"},
        ),
    )


@pytest.mark.asyncio
async def test_dispatch_sends_for_certidao_in_window(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        validade=today + timedelta(days=10),  # janela 15d
    )

    captured: list[httpx.Request] = []
    resend = _mock_resend_client(captured)
    summary = await dispatch_expiration_alerts(
        db_session,
        resend,
        recipients=["lic@primor.example"],
        today=today,
    )
    await resend.aclose()

    assert summary.sent == 1
    assert summary.skipped == 0
    assert summary.failed == 0
    assert len(captured) == 1
    body = captured[0].read().decode()
    assert "primor.example" in body
    # Subject e corpo devem refletir os 10 dias REAIS, nao a janela 15.
    # (regressao -- antes do fix `dias_restantes` o email mostrava "15 dias"
    # para uma certidao com 10 dias restantes).
    assert "10 dia" in body  # "10 dia(s)" na urgencia + subject
    assert "vence em 10 dia" in body.lower()  # subject
    assert "vence em 15 dia" not in body.lower()  # nao deve usar janela
    # Logged in DB
    log = (await db_session.execute(CertidaoAlertaLog.__table__.select())).first()
    assert log is not None


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
    resend = _mock_resend_client(captured)

    summary1 = await dispatch_expiration_alerts(
        db_session,
        resend,
        recipients=["x@y.com"],
        today=today,
    )
    summary2 = await dispatch_expiration_alerts(
        db_session,
        resend,
        recipients=["x@y.com"],
        today=today,
    )
    await resend.aclose()

    assert summary1.sent == 1
    assert summary2.sent == 0
    assert summary2.skipped == 1
    assert len(captured) == 1, "Email enviado uma so vez (idempotente)"


@pytest.mark.asyncio
async def test_dispatch_skips_when_no_validade(db_session: AsyncSession) -> None:
    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="X",
        tipo="ATESTADO_CAT",
        validade=None,
    )

    captured: list[httpx.Request] = []
    resend = _mock_resend_client(captured)
    summary = await dispatch_expiration_alerts(
        db_session, resend, recipients=["x@y.com"], today=today
    )
    await resend.aclose()
    assert summary.sent == 0
    assert summary.skipped == 1
    assert len(captured) == 0


@pytest.mark.asyncio
async def test_dispatch_retries_after_failure_without_unique_violation(
    db_session: AsyncSession,
) -> None:
    """Regressao: 1a tentativa falha grava log status=failed; 2a tentativa
    deve atualizar in-place (UPDATE), nao tentar INSERT que violaria a
    UniqueConstraint(certidao_id, janela)."""
    today = date(2026, 4, 25)
    await create_certidao(
        db_session,
        empresa_cnpj="44229813000123",
        tipo="CND_FEDERAL",
        validade=today + timedelta(days=10),  # janela 15d
    )

    fail_count = {"n": 0}

    def fail_handler(request: httpx.Request) -> httpx.Response:
        fail_count["n"] += 1
        return httpx.Response(500, json={"message": "boom"})

    fail_resend = ResendClient(
        api_key="re_test",
        client=httpx.AsyncClient(
            base_url="https://mock.resend",
            transport=httpx.MockTransport(fail_handler),
            headers={"Authorization": "Bearer re_test"},
        ),
    )
    summary1 = await dispatch_expiration_alerts(
        db_session, fail_resend, recipients=["x@y.com"], today=today
    )
    await fail_resend.aclose()
    assert summary1.failed == 1
    assert summary1.sent == 0

    # Confirma que existe exatamente 1 log com status=failed.
    logs = (await db_session.execute(CertidaoAlertaLog.__table__.select())).all()
    assert len(logs) == 1

    # Segunda rodada: ainda falha. Sem o fix, isto crasharia com IntegrityError.
    fail_resend2 = ResendClient(
        api_key="re_test",
        client=httpx.AsyncClient(
            base_url="https://mock.resend",
            transport=httpx.MockTransport(fail_handler),
            headers={"Authorization": "Bearer re_test"},
        ),
    )
    summary2 = await dispatch_expiration_alerts(
        db_session, fail_resend2, recipients=["x@y.com"], today=today
    )
    await fail_resend2.aclose()
    assert summary2.failed == 1
    logs = (await db_session.execute(CertidaoAlertaLog.__table__.select())).all()
    assert len(logs) == 1, "log deve ter sido atualizado in-place, nao duplicado"

    # Terceira rodada: agora o Resend volta. O log failed deve virar sent.
    captured: list[httpx.Request] = []
    ok_resend = _mock_resend_client(captured)
    summary3 = await dispatch_expiration_alerts(
        db_session, ok_resend, recipients=["x@y.com"], today=today
    )
    await ok_resend.aclose()
    assert summary3.sent == 1
    assert summary3.failed == 0
    logs = (await db_session.execute(CertidaoAlertaLog.__table__.select())).all()
    assert len(logs) == 1, "ainda 1 log, agora promovido a sent"


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
    """Sem destinatarios -> early return, nem tenta hit no Resend."""
    today = date(2026, 4, 25)
    await create_certidao(db_session, empresa_cnpj="X", tipo="FGTS", validade=today)
    captured: list[httpx.Request] = []
    resend = _mock_resend_client(captured)
    summary = await dispatch_expiration_alerts(db_session, resend, recipients=[], today=today)
    await resend.aclose()
    assert summary.total_certidoes == 0
    assert summary.sent == 0
    assert len(captured) == 0


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
async def test_dispatch_alerts_endpoint_503_without_resend(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Sem RESEND_API_KEY -> 503 (igual ao dispatch de boletins)."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    r = await api_client.post(
        "/api/v1/licitacoes/certidoes/dispatch-alerts",
        json={"recipients": ["x@y.com"]},
        headers=auth_headers,
    )
    assert r.status_code == 503
    assert "RESEND_API_KEY" in r.json()["detail"]


def test_janelas_alerta_constant_is_descending() -> None:
    """Sanity: as janelas precisam estar em ordem decrescente para `min` no
    janela_for_certidao funcionar quando ha mais de 1 candidata."""
    assert list(JANELAS_ALERTA) == sorted(JANELAS_ALERTA, reverse=True)
