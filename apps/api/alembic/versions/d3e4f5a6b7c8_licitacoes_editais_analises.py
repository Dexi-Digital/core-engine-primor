"""licitacoes_editais_analises

Tabela de D.5 (analise IA de editais). Uma linha por edital. Mantemos
campos promovidos (prazo, garantia, BDI, visita tecnica, valor estimado)
como colunas top-level para consultas rapidas e `data` como JSON para
flexibilidade futura (adicionar campos sem nova migracao).

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-04-23
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "licitacoes_editais_analises",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "edital_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes_editais.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="pending"
        ),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prazo_execucao_dias", sa.Integer(), nullable=True),
        sa.Column("garantia_percentual", sa.Numeric(8, 4), nullable=True),
        sa.Column("bdi_maximo_percentual", sa.Numeric(8, 4), nullable=True),
        sa.Column("visita_tecnica_obrigatoria", sa.Boolean(), nullable=True),
        sa.Column("valor_estimado", sa.Numeric(20, 2), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("anexos_analisados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_editais_analises_status",
        "licitacoes_editais_analises",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_editais_analises_status", table_name="licitacoes_editais_analises"
    )
    op.drop_table("licitacoes_editais_analises")
