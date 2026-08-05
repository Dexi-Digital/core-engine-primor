"""Tests da Squad 5 -- ciclo de contratos (demanda #12, sem assinatura)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.financeiro_contratos.models import (
    STATUS_CONTRATO,
    TIPOS_CONTRATO,
    Contrato,
    ContratoAlertaLog,
)


@pytest.mark.asyncio
async def test_contrato_model_roundtrip(db_session: AsyncSession) -> None:
    row = Contrato(
        titulo="Locacao de escavadeira CAT 320",
        contraparte_nome="TratorMax Ltda",
        contraparte_documento="12345678000190",
        tipo="locacao",
        valor=Decimal("15000.00"),
        data_inicio=date(2026, 1, 1),
        data_fim=date(2026, 12, 31),
        status="vigente",
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.id is not None
    assert row.valor == Decimal("15000.00")
    assert row.obra_id is None
    assert row.arquivo_path is None
    assert row.easyjur_ref is None
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_contrato_alerta_log_unique_janela(db_session: AsyncSession) -> None:
    contrato = Contrato(
        titulo="X",
        contraparte_nome="Y",
        tipo="fornecedor",
        data_inicio=date(2026, 1, 1),
        status="vigente",
    )
    db_session.add(contrato)
    await db_session.commit()
    db_session.add(
        ContratoAlertaLog(
            contrato_id=contrato.id, janela="30d", recipients=["a@b.com"]
        )
    )
    await db_session.commit()
    db_session.add(
        ContratoAlertaLog(
            contrato_id=contrato.id, janela="30d", recipients=["a@b.com"]
        )
    )
    with pytest.raises(IntegrityError):  # uq_contrato_alerta_janela
        await db_session.commit()
    await db_session.rollback()
    count = len(
        (await db_session.execute(select(ContratoAlertaLog.id))).scalars().all()
    )
    assert count == 1


def test_tipos_e_status_canonicos() -> None:
    assert {"cliente", "fornecedor", "locacao"} == set(dict(TIPOS_CONTRATO))
    assert {"rascunho", "vigente", "encerrado", "judicializado"} == set(
        dict(STATUS_CONTRATO)
    )
