"""D5 -- combustivel/abastecimento em parte diaria (PWA mobile)

Revision ID: cc3d4e5f6a7b
Revises: bb2c3d4e5f6a
Create Date: 2026-04-23

Adiciona campos de abastecimento em `partes_diarias` para suportar o
fluxo de apontamento manual via PWA mobile (D5 fase 2). O apontador em
campo informa litros abastecidos junto do horimetro/km, e o cliente
calcula consumo (l/h ou km/l) a partir disso.

Tambem renomeia/garante semantica de `ocr_source` aceitar "manual_pwa"
como fonte (string livre, sem migration de constraint).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "cc3d4e5f6a7b"
down_revision = "bb2c3d4e5f6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Litros abastecidos durante a jornada -- nullable porque nem todo
    # apontamento envolve abastecimento (ex.: dia em que o veiculo nao
    # rodou ou o tanque nao foi reabastecido).
    op.add_column(
        "partes_diarias",
        sa.Column("combustivel_litros", sa.Numeric(10, 2), nullable=True),
    )
    # Custo total do abastecimento (R$). Util para integrar com o
    # financeiro (D8) sem precisar replicar logica de calculo de
    # consumo.
    op.add_column(
        "partes_diarias",
        sa.Column("combustivel_custo", sa.Numeric(12, 2), nullable=True),
    )
    # UUID gerado pelo cliente (PWA mobile) para idempotencia da fila
    # offline -- se o app reenviar a mesma parte (retry depois de
    # cair conexao), o backend devolve a row existente em vez de
    # duplicar. Unique parcial: so vale para apontamentos manuais
    # (ocr_source='manual_pwa'); uploads OCR nao tem client_uuid.
    op.add_column(
        "partes_diarias",
        sa.Column("client_uuid", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_partes_diarias_client_uuid",
        "partes_diarias",
        ["client_uuid"],
        unique=True,
        postgresql_where=sa.text("client_uuid IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_partes_diarias_client_uuid", table_name="partes_diarias")
    op.drop_column("partes_diarias", "client_uuid")
    op.drop_column("partes_diarias", "combustivel_custo")
    op.drop_column("partes_diarias", "combustivel_litros")
