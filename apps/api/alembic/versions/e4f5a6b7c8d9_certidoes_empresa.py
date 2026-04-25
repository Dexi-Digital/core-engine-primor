"""certidoes_empresa

Tabela de D.6 (gestao de CNDs / atestados). Uma linha por documento da
empresa Primor (CND federal, FGTS, CNDT, atestados CAT, etc.) com
emissao, validade e arquivo opcional.

Tambem cria `certidoes_alertas_log` para registrar emails enviados de
vencimento (idempotencia: nao reenviar a mesma janela 30d/15d/7d/0d).

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-04-25
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "certidoes_empresa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("empresa_cnpj", sa.String(length=32), nullable=False, index=True),
        sa.Column("tipo", sa.String(length=64), nullable=False, index=True),
        sa.Column("numero", sa.String(length=128), nullable=True),
        sa.Column("emissao", sa.Date(), nullable=True),
        sa.Column("validade", sa.Date(), nullable=True, index=True),
        sa.Column("arquivo_path", sa.String(length=1024), nullable=True),
        sa.Column("orgao_emissor", sa.String(length=255), nullable=True),
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
        "ix_certidoes_empresa_cnpj_tipo",
        "certidoes_empresa",
        ["empresa_cnpj", "tipo"],
    )

    op.create_table(
        "certidoes_alertas_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "certidao_id",
            sa.Integer(),
            sa.ForeignKey("certidoes_empresa.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Janela do alerta: "30d" | "15d" | "7d" | "0d" (vencido) | "vencido_mensal"
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
        sa.UniqueConstraint(
            "certidao_id", "janela", name="uq_certidao_alerta_janela"
        ),
    )


def downgrade() -> None:
    op.drop_table("certidoes_alertas_log")
    op.drop_index(
        "ix_certidoes_empresa_cnpj_tipo", table_name="certidoes_empresa"
    )
    op.drop_table("certidoes_empresa")
