"""contratos

Tabelas da Squad 5 (demanda #12): `contratos` (ciclo de contratos sem
assinatura digital) e `contratos_alertas_log` (idempotencia dos alertas
de vencimento, janelas 30/15/7/0 dias -- mesmo desenho do D.6).

Revision ID: aa11bb22cc33
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "aa11bb22cc33"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contratos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("titulo", sa.String(length=255), nullable=False),
        sa.Column("contraparte_nome", sa.String(length=255), nullable=False, index=True),
        sa.Column("contraparte_documento", sa.String(length=32), nullable=True, index=True),
        sa.Column("tipo", sa.String(length=32), nullable=False, index=True),
        sa.Column(
            "obra_id",
            sa.Integer(),
            sa.ForeignKey("obras_obra.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("valor", sa.Integer(), nullable=True),
        sa.Column("data_inicio", sa.Date(), nullable=False),
        sa.Column("data_fim", sa.Date(), nullable=True, index=True),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="rascunho", index=True
        ),
        sa.Column("arquivo_path", sa.String(length=1024), nullable=True),
        sa.Column("easyjur_ref", sa.String(length=128), nullable=True),
        sa.Column("observacoes", sa.String(length=2048), nullable=True),
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
        "ix_contratos_status_data_fim", "contratos", ["status", "data_fim"]
    )
    op.create_table(
        "contratos_alertas_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "contrato_id",
            sa.Integer(),
            sa.ForeignKey("contratos.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("janela", sa.String(length=32), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("resend_message_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="sent"),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.UniqueConstraint("contrato_id", "janela", name="uq_contrato_alerta_janela"),
    )


def downgrade() -> None:
    op.drop_table("contratos_alertas_log")
    op.drop_index("ix_contratos_status_data_fim", table_name="contratos")
    op.drop_table("contratos")
