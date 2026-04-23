"""licitacoes_saved_queries + licitacoes_boletins_log

Tabelas de D.3 (boletins por email). `saved_queries` armazena os filtros
que um usuario quer receber por digest; `boletins_log` registra cada envio
feito (timestamp + maior `licitacao.id` incluido) para permitir que a
proxima execucao so considere publicacoes novas desde o ultimo envio.

Revision ID: b1c2d3e4f5a6
Revises: 3fbc4a1e8d57
Create Date: 2026-04-23
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "3fbc4a1e8d57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "licitacoes_saved_queries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("user_email", sa.String(length=255), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("uf", sa.String(length=4), nullable=True),
        sa.Column("modalidade", sa.String(length=128), nullable=True),
        sa.Column("search", sa.String(length=200), nullable=True),
        sa.Column("orgao_cnpj", sa.String(length=32), nullable=True),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
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
    op.create_index(
        "ix_saved_queries_user_email",
        "licitacoes_saved_queries",
        ["user_email"],
    )
    op.create_index(
        "ix_saved_queries_active",
        "licitacoes_saved_queries",
        ["active"],
    )

    op.create_table(
        "licitacoes_boletins_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "saved_query_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes_saved_queries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("licitacoes_count", sa.Integer(), nullable=False),
        sa.Column("last_licitacao_id", sa.Integer(), nullable=True),
        sa.Column("resend_message_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="sent"),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
    )
    op.create_index(
        "ix_boletins_log_saved_query",
        "licitacoes_boletins_log",
        ["saved_query_id", "sent_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_boletins_log_saved_query", table_name="licitacoes_boletins_log")
    op.drop_table("licitacoes_boletins_log")
    op.drop_index("ix_saved_queries_active", table_name="licitacoes_saved_queries")
    op.drop_index("ix_saved_queries_user_email", table_name="licitacoes_saved_queries")
    op.drop_table("licitacoes_saved_queries")
