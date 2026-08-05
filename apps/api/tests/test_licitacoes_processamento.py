"""Tests do processamento pos-aprovacao (Captador Squad 2)."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.licitacoes.models import (
    AnexoEdital,
    Edital,
    Licitacao,
    PastaProjeto,
    PlanilhaOrcamentaria,
)


async def _mk_licitacao(db: AsyncSession, **overrides) -> Licitacao:
    dados = dict(
        external_id="12345678000100-2026-7",
        source="pncp",
        numero_compra="002/2026",
        ano_compra=2026,
        sequencial_compra=7,
        objeto_compra="Pavimentacao asfaltica",
        orgao_cnpj="12345678000100",
        orgao_razao_social="Prefeitura de Teste",
        uf_sigla="MG",
        municipio_nome="Belo Horizonte",
    )
    dados.update(overrides)
    lic = Licitacao(**dados)
    db.add(lic)
    await db.commit()
    await db.refresh(lic)
    return lic


@pytest.mark.asyncio
async def test_pasta_projeto_unica_por_licitacao(db_session: AsyncSession) -> None:
    lic = await _mk_licitacao(db_session)
    db_session.add(
        PastaProjeto(
            licitacao_id=lic.id,
            nome_pasta="mg-belo_horizonte-prefeitura-002_2026",
            caminho=f"/tmp/editais/{lic.id}",
            storage_backend="local",
        )
    )
    await db_session.commit()

    db_session.add(
        PastaProjeto(
            licitacao_id=lic.id,
            nome_pasta="duplicada",
            caminho="/tmp/x",
            storage_backend="local",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_planilha_orcamentaria_defaults(db_session: AsyncSession) -> None:
    lic = await _mk_licitacao(db_session)
    edital = Edital(licitacao_id=lic.id, source="pncp", status="completed")
    db_session.add(edital)
    await db_session.flush()
    anexo = AnexoEdital(
        edital_id=edital.id,
        sequencial_documento=1,
        titulo="Planilha Orcamentaria",
        source_url="https://pncp.gov.br/arquivos/1",
        filename="planilha_orcamentaria.xlsx",
        storage_path="/tmp/x.xlsx",
    )
    db_session.add(anexo)
    await db_session.flush()

    db_session.add(
        PlanilhaOrcamentaria(
            licitacao_id=lic.id,
            anexo_id=anexo.id,
            nome_arquivo="planilha_orcamentaria.xlsx",
            extensao=".xlsx",
            score_classificacao=35,
            link="https://pncp.gov.br/arquivos/1",
        )
    )
    await db_session.commit()

    row = (
        await db_session.execute(select(PlanilhaOrcamentaria))
    ).scalar_one()
    assert row.status_validacao == "automatica"
    assert row.principal is False


@pytest.mark.asyncio
async def test_local_storage_ensure_project_folder(tmp_path) -> None:
    from app.modules.licitacoes.storage import LocalStorage

    storage = LocalStorage(tmp_path)
    caminho, link = await storage.ensure_project_folder(
        licitacao_id=42, nome_pasta="mg-bh-prefeitura-002_2026"
    )
    assert caminho == str(tmp_path / "42")
    assert (tmp_path / "42").is_dir()
    assert link is None
    # Idempotente: segunda chamada nao explode e retorna o mesmo caminho.
    caminho2, _ = await storage.ensure_project_folder(
        licitacao_id=42, nome_pasta="mg-bh-prefeitura-002_2026"
    )
    assert caminho2 == caminho


@pytest.mark.asyncio
async def test_storage_factory_local(tmp_path, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.modules.licitacoes.storage import LocalStorage
    from app.modules.licitacoes.storage_factory import editais_storage

    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("EDITAIS_STORAGE_PATH", str(tmp_path))
    get_settings.cache_clear()
    try:
        async with editais_storage(get_settings()) as storage:
            assert isinstance(storage, LocalStorage)
            assert storage.root == tmp_path
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_storage_factory_onedrive_sem_credenciais_usa_mock(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.integrations.onedrive.storage import OneDriveStorage
    from app.modules.licitacoes.storage_factory import editais_storage

    monkeypatch.setenv("STORAGE_BACKEND", "onedrive")
    monkeypatch.delenv("MS_GRAPH_TENANT_ID", raising=False)
    get_settings.cache_clear()
    try:
        async with editais_storage(get_settings()) as storage:
            assert isinstance(storage, OneDriveStorage)
    finally:
        get_settings.cache_clear()
