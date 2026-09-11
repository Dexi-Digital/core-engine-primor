"""O seed de demonstracao precisa encher as telas de Frota e Contratos.

Contexto: o primeiro deploy foi ao ar com schema criado e so 6 tabelas
semeadas. Manutencao & Frota tem 2.883 linhas de codigo e 5 telas, mas
abria vazia -- e a leitura de quem olhava era "o sistema esta igual a
meses atras", quando na verdade era banco sem dados.

Fiscal, Ponto e TOTVS ficam FORA do seed de proposito: ali o valor esta
em ver a integracao trazendo o dado.
"""
from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.modules.financeiro_contratos.models import (
    STATUS_CONTRATO_VALIDOS,
    TIPOS_CONTRATO_VALIDOS,
    Contrato,
)
from app.modules.manutencao_frota.models import (
    PARTE_STATUSES,
    STATUSES_VALIDOS,
    TIPOS_DOC_VALIDOS,
    DocumentoVeiculo,
    ParteDiaria,
    Veiculo,
)
from scripts import seed_dossie


@pytest_asyncio.fixture
async def seed_db(monkeypatch: pytest.MonkeyPatch):
    """Banco proprio + `SessionLocal` do seed apontado para ele.

    O script usa o engine real (`app.core.db.SessionLocal`); sem o
    monkeypatch o teste tentaria escrever no Postgres de
    desenvolvimento. `StaticPool` faz todas as conexoes compartilharem
    o mesmo SQLite em memoria -- o seed abre varias sessoes, e sem isso
    cada uma veria um banco vazio diferente.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(seed_dossie, "SessionLocal", factory)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_seed_planta_veiculos_e_documentos(seed_db: AsyncSession):
    criados = await seed_dossie._upsert_veiculos()
    assert criados > 0

    veiculos = (await seed_db.execute(select(Veiculo))).scalars().all()
    assert len(veiculos) == len(seed_dossie._VEICULOS)
    assert all(v.status in STATUSES_VALIDOS for v in veiculos)
    assert len({v.placa for v in veiculos}) == len(veiculos), "placas duplicadas"

    docs = (await seed_db.execute(select(DocumentoVeiculo))).scalars().all()
    assert docs
    assert all(d.tipo in TIPOS_DOC_VALIDOS for d in docs)


@pytest.mark.asyncio
async def test_seed_inclui_documento_vencido_e_vencendo(
    seed_db: AsyncSession,
):
    """Sem isso o painel de frota abre todo verde e nao exercita os
    alertas -- mesma escolha ja feita nas certidoes do dossie."""
    await seed_dossie._upsert_veiculos()
    docs = (await seed_db.execute(select(DocumentoVeiculo))).scalars().all()
    hoje = date.today()
    prazos = [(d.validade - hoje).days for d in docs if d.validade]

    assert any(p < 0 for p in prazos), "nenhum documento vencido"
    assert any(0 <= p <= 30 for p in prazos), "nenhum documento vencendo"
    assert any(p > 30 for p in prazos), "nenhum documento em dia"


@pytest.mark.asyncio
async def test_seed_planta_partes_diarias_em_estagios_diferentes(
    seed_db: AsyncSession,
):
    """Uma parte fica `pendente` de proposito: sem worker Celery no ar
    e assim que o pipeline realmente se comporta."""
    await seed_dossie._upsert_veiculos()
    criadas = await seed_dossie._upsert_partes_diarias()
    assert criadas > 0

    partes = (await seed_db.execute(select(ParteDiaria))).scalars().all()
    assert all(p.ocr_status in PARTE_STATUSES for p in partes)
    assert len({p.ocr_status for p in partes}) >= 2
    assert any(p.ocr_status == "pendente" for p in partes)
    assert all(p.veiculo_id is not None for p in partes), "parte sem veiculo"


@pytest.mark.asyncio
async def test_seed_planta_contratos_validos(seed_db: AsyncSession):
    criados = await seed_dossie._upsert_contratos()
    assert criados > 0

    contratos = (await seed_db.execute(select(Contrato))).scalars().all()
    assert all(c.tipo in TIPOS_CONTRATO_VALIDOS for c in contratos)
    assert all(c.status in STATUS_CONTRATO_VALIDOS for c in contratos)
    assert all(c.data_fim is not None for c in contratos)
    # Um vigente perto do vencimento faz o `vencimento_status` sair de
    # "vigente" na tela -- e o que prova o calculo funcionando.
    hoje = date.today()
    assert any(
        c.status == "vigente" and 0 <= (c.data_fim - hoje).days <= 30
        for c in contratos
    )


@pytest.mark.asyncio
async def test_seed_e_idempotente(seed_db: AsyncSession):
    """Roda no boot do container a cada deploy (SEED_DEMO=1) -- rodar
    duas vezes nao pode duplicar."""
    await seed_dossie._upsert_veiculos()
    await seed_dossie._upsert_partes_diarias()
    await seed_dossie._upsert_contratos()

    assert await seed_dossie._upsert_veiculos() == 0
    assert await seed_dossie._upsert_partes_diarias() == 0
    assert await seed_dossie._upsert_contratos() == 0

    veiculos = (await seed_db.execute(select(Veiculo))).scalars().all()
    assert len(veiculos) == len(seed_dossie._VEICULOS)
