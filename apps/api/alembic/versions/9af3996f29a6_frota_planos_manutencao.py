"""Planos de manutencao por equipamento (revisoes programadas).

Tira da planilha do SharePoint o "controle de revisoes": intervalo por
equipamento, marcador da ultima revisao e vencimento medido em USO
(horimetro/odometro), nao em calendario.

Revision ID: 9af3996f29a6
Revises: 523a1b36b275
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "9af3996f29a6"
down_revision = "523a1b36b275"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "frota_planos_manutencao",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "veiculo_id",
            sa.Integer(),
            sa.ForeignKey("frota_veiculos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("descricao", sa.String(length=200), nullable=False),
        sa.Column("base", sa.String(length=8), nullable=False),
        sa.Column("intervalo", sa.Numeric(12, 2), nullable=False),
        sa.Column("ultima_revisao_em", sa.Date(), nullable=True),
        sa.Column("ultima_revisao_marcador", sa.Numeric(12, 2), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column(
            "ativo",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Varios planos por veiculo (oleo, filtro, correia) -- indice NAO
    # unico de proposito.
    op.create_index(
        "ix_frota_planos_manutencao_veiculo_id",
        "frota_planos_manutencao",
        ["veiculo_id"],
    )
    op.create_index(
        "ix_frota_planos_manutencao_base", "frota_planos_manutencao", ["base"]
    )
    op.create_index(
        "ix_frota_planos_manutencao_ativo", "frota_planos_manutencao", ["ativo"]
    )


def downgrade() -> None:
    op.drop_index("ix_frota_planos_manutencao_ativo", "frota_planos_manutencao")
    op.drop_index("ix_frota_planos_manutencao_base", "frota_planos_manutencao")
    op.drop_index(
        "ix_frota_planos_manutencao_veiculo_id", "frota_planos_manutencao"
    )
    op.drop_table("frota_planos_manutencao")
