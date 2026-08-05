"""Tests do workflow de triagem do Captador (Squad 1) e do processamento
pos-aprovacao (Squad 2: processar-anexos, planilhas, task Celery)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.main import app
from app.modules.licitacoes import triagem
from app.modules.licitacoes.models import DecisaoTriagem, Licitacao
from app.modules.licitacoes.processamento import (
    STATUS_APROVADO,
    STATUS_NOVO_CAPTADO,
)
from app.modules.licitacoes.router import (
    get_editais_storage,
    get_pncp_client,
)
from tests.test_licitacoes_processamento import (
    FakePncp,
)
from tests.test_licitacoes_processamento import (
    _mk_licitacao as _mk_licitacao_processamento,
)


class TestMaquinaDeStatus:
    def test_status_validos_cobrem_pdf_do_cliente(self) -> None:
        assert frozenset(
            {
                "novo_captado",
                "em_analise",
                "aprovado",
                "rejeitado",
                "processando_anexos",
                "completo",
                "sem_planilha",
                "erro_portal",
                "erro_sharepoint",
            }
        ) == triagem.STATUS_VALIDOS

    @pytest.mark.parametrize(
        ("atual", "novo"),
        [
            ("novo_captado", "em_analise"),
            ("novo_captado", "aprovado"),
            ("novo_captado", "rejeitado"),
            ("em_analise", "aprovado"),
            ("em_analise", "rejeitado"),
            ("em_analise", "novo_captado"),
            ("aprovado", "processando_anexos"),
            ("processando_anexos", "completo"),
            ("processando_anexos", "sem_planilha"),
            ("processando_anexos", "erro_portal"),
            ("processando_anexos", "erro_sharepoint"),
            ("erro_portal", "processando_anexos"),
            ("erro_sharepoint", "processando_anexos"),
            ("sem_planilha", "processando_anexos"),
            ("completo", "processando_anexos"),  # reprocesso/re-identificacao manual
        ],
    )
    def test_transicoes_permitidas(self, atual: str, novo: str) -> None:
        triagem.validar_transicao(atual, novo)  # nao levanta

    @pytest.mark.parametrize(
        ("atual", "novo"),
        [
            ("aprovado", "rejeitado"),  # aprovado nao pode ser rejeitado depois
            ("rejeitado", "aprovado"),  # rejeitado e terminal
            ("rejeitado", "processando_anexos"),  # rejeitado e terminal
            ("completo", "novo_captado"),  # completo nao volta pra novo_captado
            ("novo_captado", "completo"),  # nao pula a aprovacao
            ("novo_captado", "processando_anexos"),
        ],
    )
    def test_transicoes_proibidas(self, atual: str, novo: str) -> None:
        with pytest.raises(triagem.TransicaoInvalidaError) as exc:
            triagem.validar_transicao(atual, novo)
        assert exc.value.atual == atual
        assert exc.value.novo == novo

    def test_status_desconhecido_levanta(self) -> None:
        with pytest.raises(triagem.TransicaoInvalidaError):
            triagem.validar_transicao("banana", "aprovado")
        with pytest.raises(triagem.TransicaoInvalidaError):
            triagem.validar_transicao("novo_captado", "banana")


def _mk_licitacao(external_id: str = "trg-1") -> Licitacao:
    return Licitacao(
        external_id=external_id,
        source="pncp",
        objeto_compra="Pavimentacao asfaltica em vias urbanas",
        uf_sigla="MG",
        municipio_nome="Belo Horizonte",
        modalidade_nome="Pregao Eletronico",
    )


@pytest.mark.asyncio
async def test_licitacao_nasce_novo_captado_e_decisao_persiste(db_session) -> None:
    lic = _mk_licitacao()
    db_session.add(lic)
    await db_session.commit()
    assert lic.status_triagem == "novo_captado"

    db_session.add(
        DecisaoTriagem(
            licitacao_id=lic.id,
            decisao="aprovado",
            observacao=None,
            usuario_email="analista@primor.com",
        )
    )
    await db_session.commit()

    row = await db_session.scalar(
        select(DecisaoTriagem).where(DecisaoTriagem.licitacao_id == lic.id)
    )
    assert row is not None
    assert row.decisao == "aprovado"
    assert row.usuario_email == "analista@primor.com"
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_aprovar_muda_status_grava_decisao_e_audit(db_session) -> None:
    lic = _mk_licitacao("trg-aprovar")
    db_session.add(lic)
    await db_session.commit()

    decisao = await triagem.aprovar(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="dentro do perfil de engenharia",
    )

    assert decisao.decisao == "aprovado"
    await db_session.refresh(lic)
    assert lic.status_triagem == "aprovado"

    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.resource == "licitacoes.triagem")
    )
    assert audit is not None
    assert audit.actor == "analista@primor.com"
    assert audit.action == "triagem.aprovar"
    assert audit.resource_id == str(lic.id)


@pytest.mark.asyncio
async def test_rejeitar_exige_motivo(db_session) -> None:
    lic = _mk_licitacao("trg-rejeitar-sem-motivo")
    db_session.add(lic)
    await db_session.commit()

    with pytest.raises(ValueError, match="motivo"):
        await triagem.rejeitar(
            db_session,
            licitacao_id=lic.id,
            usuario_email="analista@primor.com",
            observacao="   ",
        )
    await db_session.refresh(lic)
    assert lic.status_triagem == "novo_captado"


@pytest.mark.asyncio
async def test_rejeitar_com_motivo_e_terminal(db_session) -> None:
    lic = _mk_licitacao("trg-rejeitar")
    db_session.add(lic)
    await db_session.commit()

    await triagem.rejeitar(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="objeto fora do perfil (merenda escolar)",
    )
    await db_session.refresh(lic)
    assert lic.status_triagem == "rejeitado"

    with pytest.raises(triagem.TransicaoInvalidaError):
        await triagem.aprovar(
            db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
        )


@pytest.mark.asyncio
async def test_observacao_move_para_em_analise(db_session) -> None:
    lic = _mk_licitacao("trg-obs")
    db_session.add(lic)
    await db_session.commit()

    await triagem.registrar_observacao(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="aguardando planilha no portal",
    )
    await db_session.refresh(lic)
    assert lic.status_triagem == "em_analise"

    # segunda observacao nao muda mais o status
    await triagem.registrar_observacao(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="portal voltou",
    )
    await db_session.refresh(lic)
    assert lic.status_triagem == "em_analise"

    rows = await triagem.listar_decisoes(db_session, licitacao_id=lic.id)
    assert len(rows) == 2
    assert rows[0].observacao == "portal voltou"  # mais recente primeiro


@pytest.mark.asyncio
async def test_aprovar_licitacao_inexistente(db_session) -> None:
    with pytest.raises(LookupError):
        await triagem.aprovar(
            db_session, licitacao_id=99999, usuario_email="a@primor.com"
        )


@pytest.mark.asyncio
async def test_aprovar_dispara_celery_quando_flag_ligada(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("CAPTADOR_AUTO_PROCESS", "1")
    get_settings.cache_clear()

    enviados: list[tuple[str, list, str]] = []

    class FakeDispatcher:
        def send_task(self, name: str, args: list, queue: str) -> None:
            enviados.append((name, args, queue))

    monkeypatch.setattr(triagem, "_get_celery_dispatcher", lambda: FakeDispatcher())

    lic = _mk_licitacao("trg-celery")
    db_session.add(lic)
    await db_session.commit()
    await triagem.aprovar(
        db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
    )

    assert enviados == [
        ("worker.tasks.licitacoes.processar_edital_aprovado", [lic.id], "licitacoes")
    ]
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_aprovar_sobrevive_broker_fora(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem a task da Squad 2 registrada / broker down, aprovar NAO pode falhar."""
    from app.core.config import get_settings

    monkeypatch.setenv("CAPTADOR_AUTO_PROCESS", "1")
    get_settings.cache_clear()

    def _boom() -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(triagem, "_get_celery_dispatcher", _boom)

    lic = _mk_licitacao("trg-broker-down")
    db_session.add(lic)
    await db_session.commit()
    decisao = await triagem.aprovar(
        db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
    )
    assert decisao.decisao == "aprovado"
    await db_session.refresh(lic)
    assert lic.status_triagem == "aprovado"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_aprovar_nao_dispara_celery_por_default(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamado: list[bool] = []
    monkeypatch.setattr(
        triagem, "_get_celery_dispatcher", lambda: chamado.append(True)
    )
    lic = _mk_licitacao("trg-sem-flag")
    db_session.add(lic)
    await db_session.commit()
    await triagem.aprovar(
        db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
    )
    assert chamado == []


@pytest.mark.asyncio
async def test_endpoint_aprovar_e_historico(
    api_client, db_session, auth_headers
) -> None:
    lic = _mk_licitacao("trg-api-aprovar")
    db_session.add(lic)
    await db_session.commit()

    r = await api_client.post(
        f"/api/v1/licitacoes/{lic.id}/triagem/aprovar",
        json={"observacao": "perfil ok"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decisao"] == "aprovado"
    assert body["usuario_email"] == "test-admin@primor.com"

    r2 = await api_client.get(f"/api/v1/licitacoes/{lic.id}/triagem")
    assert r2.status_code == 200
    assert [d["decisao"] for d in r2.json()] == ["aprovado"]

    # status aparece no read da licitacao
    r3 = await api_client.get(f"/api/v1/licitacoes/{lic.id}")
    assert r3.json()["status_triagem"] == "aprovado"


@pytest.mark.asyncio
async def test_endpoint_aprovar_exige_jwt(api_client, db_session) -> None:
    lic = _mk_licitacao("trg-api-401")
    db_session.add(lic)
    await db_session.commit()
    r = await api_client.post(
        f"/api/v1/licitacoes/{lic.id}/triagem/aprovar", json={}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_rejeitar_sem_motivo_422(
    api_client, db_session, auth_headers
) -> None:
    lic = _mk_licitacao("trg-api-422")
    db_session.add(lic)
    await db_session.commit()
    r = await api_client.post(
        f"/api/v1/licitacoes/{lic.id}/triagem/rejeitar",
        json={"observacao": "ok"},  # < 5 chars
        headers=auth_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_endpoint_transicao_invalida_409(
    api_client, db_session, auth_headers
) -> None:
    lic = _mk_licitacao("trg-api-409")
    lic.status_triagem = "rejeitado"
    db_session.add(lic)
    await db_session.commit()
    r = await api_client.post(
        f"/api/v1/licitacoes/{lic.id}/triagem/aprovar",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert "rejeitado" in r.json()["detail"]


@pytest.mark.asyncio
async def test_endpoint_404(api_client, auth_headers) -> None:
    r = await api_client.post(
        "/api/v1/licitacoes/99999/triagem/aprovar",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 404
    r2 = await api_client.get("/api/v1/licitacoes/99999/triagem")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_list_filtra_por_status_triagem_e_municipio(
    api_client, db_session
) -> None:
    a = _mk_licitacao("trg-f1")  # BH / novo_captado
    b = _mk_licitacao("trg-f2")
    b.municipio_nome = "Uberlandia"
    b.status_triagem = "aprovado"
    db_session.add_all([a, b])
    await db_session.commit()

    r = await api_client.get("/api/v1/licitacoes?status_triagem=aprovado")
    assert [x["external_id"] for x in r.json()["data"]] == ["trg-f2"]

    r2 = await api_client.get("/api/v1/licitacoes?municipio=belo")
    assert [x["external_id"] for x in r2.json()["data"]] == ["trg-f1"]

    r3 = await api_client.get(
        "/api/v1/licitacoes?status_triagem=aprovado&municipio=uberl"
    )
    assert r3.json()["total"] == 1


# --- Captador Squad 2: processar-anexos, planilhas, task Celery ------------


def _override_deps(tmp_path, pncp=None):
    from app.modules.licitacoes.storage import LocalStorage

    async def _storage():
        yield LocalStorage(tmp_path)

    def _pncp():
        return pncp or FakePncp()

    app.dependency_overrides[get_editais_storage] = _storage
    app.dependency_overrides[get_pncp_client] = _pncp


def _clear_deps():
    app.dependency_overrides.pop(get_editais_storage, None)
    app.dependency_overrides.pop(get_pncp_client, None)


@pytest.mark.asyncio
async def test_processar_anexos_endpoint_exige_auth(
    api_client: AsyncClient, db_session: AsyncSession, tmp_path
) -> None:
    lic = await _mk_licitacao_processamento(db_session)
    resp = await api_client.post(f"/api/v1/licitacoes/{lic.id}/processar-anexos")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_processar_anexos_endpoint_completo(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path,
    auth_headers: dict[str, str],
) -> None:
    lic = await _mk_licitacao_processamento(db_session)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            f"/api/v1/licitacoes/{lic.id}/processar-anexos", headers=auth_headers
        )
    finally:
        _clear_deps()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status_triagem"] == "completo"
    assert body["planilha_encontrada"] is True


@pytest.mark.asyncio
async def test_processar_anexos_status_invalido_da_409(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path,
    auth_headers: dict[str, str],
) -> None:
    lic = await _mk_licitacao_processamento(
        db_session, external_id="y-1", sequencial_compra=21
    )
    assert lic.status_triagem == STATUS_NOVO_CAPTADO
    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            f"/api/v1/licitacoes/{lic.id}/processar-anexos", headers=auth_headers
        )
    finally:
        _clear_deps()
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_processar_anexos_licitacao_inexistente_da_404(
    api_client: AsyncClient, tmp_path, auth_headers: dict[str, str]
) -> None:
    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            "/api/v1/licitacoes/999999/processar-anexos", headers=auth_headers
        )
    finally:
        _clear_deps()
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_planilha_principal(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path,
    auth_headers: dict[str, str],
) -> None:
    from app.modules.licitacoes.models import PlanilhaOrcamentaria

    lic = await _mk_licitacao_processamento(
        db_session, external_id="y-2", sequencial_compra=22
    )
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            f"/api/v1/licitacoes/{lic.id}/processar-anexos", headers=auth_headers
        )
        assert resp.status_code == 200
        planilha = (
            await db_session.execute(
                select(PlanilhaOrcamentaria).where(
                    PlanilhaOrcamentaria.licitacao_id == lic.id
                )
            )
        ).scalars().first()
        resp2 = await api_client.patch(
            f"/api/v1/licitacoes/{lic.id}/planilhas/{planilha.id}",
            json={"principal": True},
            headers=auth_headers,
        )
    finally:
        _clear_deps()
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["status_validacao"] == "principal_manual"


def test_worker_task_processar_edital_aprovado_registrada() -> None:
    import sys
    from pathlib import Path

    workers_dir = Path(__file__).resolve().parents[2] / "workers"
    sys.path.insert(0, str(workers_dir))
    try:
        import worker.tasks.licitacoes  # noqa: F401
        from worker.main import celery_app  # noqa: F401

        assert (
            "worker.tasks.licitacoes.processar_edital_aprovado"
            in celery_app.tasks
        )
    finally:
        sys.path.remove(str(workers_dir))


@pytest.mark.asyncio
async def test_run_processamento_fecha_pncp_e_storage_no_context_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nota do review da Task 4: garante que o worker fecha os clients.

    `_run_processamento` abre um `PncpClient` e um `EditaisStorage` (via
    `editais_storage`) por chamada -- sem fechar, cada task Celery vaza
    um pool de conexoes httpx.
    """
    import sys
    from contextlib import asynccontextmanager
    from pathlib import Path

    workers_dir = Path(__file__).resolve().parents[2] / "workers"
    sys.path.insert(0, str(workers_dir))
    try:
        import worker.tasks.licitacoes as worker_tasks

        pncp_closed: list[bool] = []
        storage_closed: list[bool] = []

        class FakePncpClient:
            def __init__(self, base_url: str | None = None) -> None:
                self.base_url = base_url

            async def aclose(self) -> None:
                pncp_closed.append(True)

        @asynccontextmanager
        async def fake_editais_storage(settings):
            try:
                yield object()
            finally:
                storage_closed.append(True)

        class FakeResult:
            def model_dump(self) -> dict[str, object]:
                return {"ok": True}

        async def fake_processar_aprovado(db, *, licitacao_id, pncp, storage):
            return FakeResult()

        class FakeSessionCtx:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *exc_info: object) -> bool:
                return False

        monkeypatch.setattr("app.core.db.SessionLocal", lambda: FakeSessionCtx())
        monkeypatch.setattr(
            "app.integrations.pncp.client.PncpClient", FakePncpClient
        )
        monkeypatch.setattr(
            "app.modules.licitacoes.storage_factory.editais_storage",
            fake_editais_storage,
        )
        monkeypatch.setattr(
            "app.modules.licitacoes.processamento.processar_aprovado",
            fake_processar_aprovado,
        )

        result = await worker_tasks._run_processamento(123)
    finally:
        sys.path.remove(str(workers_dir))

    assert result == {"ok": True}
    assert pncp_closed == [True]
    assert storage_closed == [True]


# --- GET /licitacoes/triagem ----------------------------------------------


@pytest.mark.asyncio
async def test_triagem_lista_com_links(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path,
    auth_headers: dict[str, str],
) -> None:
    lic = await _mk_licitacao_processamento(
        db_session, external_id="y-3", sequencial_compra=23
    )
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    _override_deps(tmp_path)
    try:
        resp = await api_client.post(
            f"/api/v1/licitacoes/{lic.id}/processar-anexos", headers=auth_headers
        )
        assert resp.status_code == 200
        resp = await api_client.get("/api/v1/licitacoes/triagem")
    finally:
        _clear_deps()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    row = next(r for r in body["data"] if r["licitacao_id"] == lic.id)
    assert row["status_triagem"] == "completo"
    assert row["link_portal"] == (
        "https://pncp.gov.br/app/editais/12345678000100/2026/23"
    )
    assert row["link_planilha"] == "https://pncp.gov.br/arquivos/2"
    assert row["planilha_nome"] is not None
    assert row["anexos_count"] == 2


@pytest.mark.asyncio
async def test_triagem_filtra_por_status(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    lic = await _mk_licitacao_processamento(
        db_session, external_id="y-4", sequencial_compra=24
    )
    assert lic.status_triagem == STATUS_NOVO_CAPTADO

    resp = await api_client.get(
        "/api/v1/licitacoes/triagem", params={"status": "novo_captado"}
    )
    assert resp.status_code == 200
    assert all(
        r["status_triagem"] == "novo_captado" for r in resp.json()["data"]
    )


@pytest.mark.asyncio
async def test_triagem_inclui_observacao_da_ultima_decisao(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.modules.licitacoes.models import DecisaoTriagem

    lic = await _mk_licitacao_processamento(
        db_session, external_id="y-5", sequencial_compra=25
    )
    db_session.add(
        DecisaoTriagem(
            licitacao_id=lic.id,
            decisao="em_analise",
            observacao="verificar atestado de capacidade",
            usuario_email="analista@primor.com",
        )
    )
    await db_session.commit()

    resp = await api_client.get("/api/v1/licitacoes/triagem")
    assert resp.status_code == 200
    row = next(
        r for r in resp.json()["data"] if r["licitacao_id"] == lic.id
    )
    assert row["observacao"] == "verificar atestado de capacidade"
