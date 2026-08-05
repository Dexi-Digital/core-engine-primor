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


# --- processar_aprovado ----------------------------------------------------

XLSX_PLANILHA = None  # preenchido no primeiro uso para nao pagar o custo em import


def _xlsx_planilha_bytes() -> bytes:
    global XLSX_PLANILHA
    if XLSX_PLANILHA is None:
        import io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Item", "Unid", "Quant", "Preço Unitário", "Total"])
        buf = io.BytesIO()
        wb.save(buf)
        XLSX_PLANILHA = buf.getvalue()
    return XLSX_PLANILHA


class FakePncp:
    """Duble do PncpClient: 2 anexos, um PDF e uma planilha XLSX."""

    def __init__(self, arquivos=None, fail=False) -> None:
        self._fail = fail
        from app.integrations.pncp.client import PncpArquivo

        self.arquivos = arquivos if arquivos is not None else [
            PncpArquivo(
                sequencial_documento=1,
                titulo="EDITAL_PREGAO_002",
                tipo_documento_descricao="Edital",
                url="https://pncp.gov.br/arquivos/1",
                status_ativo=True,
                data_publicacao_pncp=None,
            ),
            PncpArquivo(
                sequencial_documento=2,
                titulo="Planilha_Orcamentaria",
                tipo_documento_descricao="Outros",
                url="https://pncp.gov.br/arquivos/2",
                status_ativo=True,
                data_publicacao_pncp=None,
            ),
        ]

    async def list_arquivos(self, *, cnpj, ano, sequencial):
        import httpx

        if self._fail:
            raise httpx.ConnectError("pncp fora do ar")
        return self.arquivos

    async def stream_arquivo(self, url):
        async def chunks():
            if url.endswith("/2"):
                yield _xlsx_planilha_bytes()
            else:
                yield b"%PDF-1.7 conteudo"

        filename = "uivd1biu.xlsx" if url.endswith("/2") else "uivd1biu.pdf"
        ct = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if url.endswith("/2")
            else "application/pdf"
        )
        return chunks(), filename, ct

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_processar_aprovado_fluxo_completo(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_COMPLETO,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    result = await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(),
        storage=LocalStorage(tmp_path),
    )

    assert result.status_triagem == STATUS_COMPLETO
    assert result.anexos_count == 2
    assert result.planilha_encontrada is True

    await db_session.refresh(lic)
    assert lic.status_triagem == STATUS_COMPLETO

    pasta = (await db_session.execute(select(PastaProjeto))).scalar_one()
    assert pasta.licitacao_id == lic.id
    assert pasta.nome_pasta.startswith("mg-belo_horizonte")

    planilha = (
        await db_session.execute(
            select(PlanilhaOrcamentaria).where(PlanilhaOrcamentaria.principal)
        )
    ).scalar_one()
    assert planilha.nome_arquivo.endswith(".xlsx")
    assert planilha.link == "https://pncp.gov.br/arquivos/2"
    assert planilha.score_classificacao >= 10


@pytest.mark.asyncio
async def test_processar_aprovado_sem_planilha(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.integrations.pncp.client import PncpArquivo
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_SEM_PLANILHA,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-8", sequencial_compra=8)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    so_pdf = [
        PncpArquivo(
            sequencial_documento=1,
            titulo="EDITAL",
            tipo_documento_descricao="Edital",
            url="https://pncp.gov.br/arquivos/1",
            status_ativo=True,
            data_publicacao_pncp=None,
        )
    ]
    result = await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(arquivos=so_pdf),
        storage=LocalStorage(tmp_path),
    )
    assert result.status_triagem == STATUS_SEM_PLANILHA
    assert result.planilha_encontrada is False


@pytest.mark.asyncio
async def test_processar_aprovado_pncp_fora_vira_erro_portal(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_ERRO_PORTAL,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-9", sequencial_compra=9)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    result = await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(fail=True),
        storage=LocalStorage(tmp_path),
    )
    assert result.status_triagem == STATUS_ERRO_PORTAL
    assert result.error_message


@pytest.mark.asyncio
async def test_processar_rejeitado_e_bloqueado(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.modules.licitacoes.processamento import (
        STATUS_REJEITADO,
        ProcessamentoNaoPermitido,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-10", sequencial_compra=10)
    lic.status_triagem = STATUS_REJEITADO
    await db_session.commit()

    with pytest.raises(ProcessamentoNaoPermitido):
        await processar_aprovado(
            db_session,
            licitacao_id=lic.id,
            pncp=FakePncp(),
            storage=LocalStorage(tmp_path),
        )


@pytest.mark.asyncio
async def test_reprocessar_completo_e_idempotente(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_COMPLETO,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-11", sequencial_compra=11)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    storage = LocalStorage(tmp_path)
    await processar_aprovado(
        db_session, licitacao_id=lic.id, pncp=FakePncp(), storage=storage
    )
    result2 = await processar_aprovado(
        db_session, licitacao_id=lic.id, pncp=FakePncp(), storage=storage
    )
    assert result2.status_triagem == STATUS_COMPLETO

    pastas = (await db_session.execute(select(PastaProjeto))).scalars().all()
    planilhas = (
        await db_session.execute(select(PlanilhaOrcamentaria))
    ).scalars().all()
    assert len([p for p in pastas if p.licitacao_id == lic.id]) == 1
    assert len([p for p in planilhas if p.licitacao_id == lic.id]) == 1


@pytest.mark.asyncio
async def test_marcar_planilha_principal_e_sticky(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.integrations.pncp.client import PncpArquivo
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        marcar_planilha_principal,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-12", sequencial_compra=12)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    duas_planilhas = [
        PncpArquivo(
            sequencial_documento=1,
            titulo="Planilha_Orcamentaria",
            tipo_documento_descricao="Outros",
            url="https://pncp.gov.br/arquivos/2",
            status_ativo=True,
            data_publicacao_pncp=None,
        ),
        PncpArquivo(
            sequencial_documento=2,
            titulo="Cronograma",
            tipo_documento_descricao="Outros",
            url="https://pncp.gov.br/arquivos/2",
            status_ativo=True,
            data_publicacao_pncp=None,
        ),
    ]
    storage = LocalStorage(tmp_path)
    await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(arquivos=duas_planilhas),
        storage=storage,
    )
    rows = (
        (await db_session.execute(select(PlanilhaOrcamentaria))).scalars().all()
    )
    rows = [r for r in rows if r.licitacao_id == lic.id]
    assert len(rows) == 2
    secundaria = next(r for r in rows if not r.principal)

    escolhida = await marcar_planilha_principal(
        db_session, licitacao_id=lic.id, planilha_id=secundaria.id
    )
    assert escolhida.principal is True
    assert escolhida.status_validacao == "principal_manual"

    # Reprocessar NAO desfaz a escolha manual.
    await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(arquivos=duas_planilhas),
        storage=storage,
    )
    await db_session.refresh(escolhida)
    assert escolhida.principal is True


@pytest.mark.asyncio
async def test_processar_aprovado_sem_triplet_pncp_vira_erro_portal(
    db_session: AsyncSession, tmp_path
) -> None:
    """Licitacao sem cnpj/ano/sequencial: `download_edital_for_licitacao`
    levanta ValueError, que deve virar erro_portal e nao vazar como 500."""
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_ERRO_PORTAL,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(
        db_session,
        external_id="x-2026-16",
        orgao_cnpj=None,
        ano_compra=None,
        sequencial_compra=None,
    )
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    result = await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(),
        storage=LocalStorage(tmp_path),
    )
    assert result.status_triagem == STATUS_ERRO_PORTAL
    assert result.error_message

    await db_session.refresh(lic)
    assert lic.status_triagem == STATUS_ERRO_PORTAL


@pytest.mark.asyncio
async def test_reprocessar_storage_falha_preserva_score_e_principal(
    db_session: AsyncSession, tmp_path
) -> None:
    """Se o arquivo sumiu do storage (nao e extensao inelegivel), o
    re-score nao pode rebaixar uma classificacao ja feita com o conteudo
    em maos -- senao a planilha principal e limpa por acidente."""
    import shutil

    from app.modules.licitacoes.identificacao_planilha import LIMIAR_PRINCIPAL
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_COMPLETO,
        processar_aprovado,
    )
    from app.modules.licitacoes.storage import LocalStorage

    lic = await _mk_licitacao(db_session, external_id="x-2026-17", sequencial_compra=17)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    storage = LocalStorage(tmp_path)
    await processar_aprovado(
        db_session, licitacao_id=lic.id, pncp=FakePncp(), storage=storage
    )
    planilha = (
        await db_session.execute(
            select(PlanilhaOrcamentaria).where(PlanilhaOrcamentaria.principal)
        )
    ).scalar_one()
    score_original = planilha.score_classificacao
    assert score_original >= LIMIAR_PRINCIPAL

    # Simula arquivo sumido do storage: a pasta fisica some, entao a
    # leitura do anexo ja baixado passa a falhar com FileNotFoundError.
    shutil.rmtree(tmp_path / str(lic.id))

    result = await processar_aprovado(
        db_session, licitacao_id=lic.id, pncp=FakePncp(), storage=storage
    )
    assert result.status_triagem == STATUS_COMPLETO

    await db_session.refresh(planilha)
    assert planilha.score_classificacao == score_original
    assert planilha.principal is True


class FakeStorageSharePointFalha:
    """Duble de `EditaisStorage`: download funciona normalmente (delega
    a uma `LocalStorage` real), mas `ensure_project_folder` explode como
    se o SharePoint estivesse fora do ar."""

    def __init__(self, tmp_path) -> None:
        from app.modules.licitacoes.storage import LocalStorage

        self._inner = LocalStorage(tmp_path)

    async def save(self, *, licitacao_id, filename, content):
        return await self._inner.save(
            licitacao_id=licitacao_id, filename=filename, content=content
        )

    async def read(self, storage_path):
        return await self._inner.read(storage_path)

    async def delete(self, storage_path):
        return await self._inner.delete(storage_path)

    async def ensure_project_folder(self, *, licitacao_id, nome_pasta):
        raise OSError("sharepoint indisponivel")


@pytest.mark.asyncio
async def test_processar_aprovado_pasta_falha_vira_erro_sharepoint(
    db_session: AsyncSession, tmp_path
) -> None:
    from app.modules.licitacoes.processamento import (
        STATUS_APROVADO,
        STATUS_ERRO_SHAREPOINT,
        processar_aprovado,
    )

    lic = await _mk_licitacao(db_session, external_id="x-2026-18", sequencial_compra=18)
    lic.status_triagem = STATUS_APROVADO
    await db_session.commit()

    result = await processar_aprovado(
        db_session,
        licitacao_id=lic.id,
        pncp=FakePncp(),
        storage=FakeStorageSharePointFalha(tmp_path),
    )
    assert result.status_triagem == STATUS_ERRO_SHAREPOINT
    assert result.error_message

    await db_session.refresh(lic)
    assert lic.status_triagem == STATUS_ERRO_SHAREPOINT
