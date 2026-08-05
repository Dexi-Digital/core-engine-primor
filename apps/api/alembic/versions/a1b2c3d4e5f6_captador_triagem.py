"""captador triagem: status_triagem em licitacoes + decisoes (Squad 1)

Revision ID: a1b2c3d4e5f6
Revises: b8c9d0e1f2a3
Create Date: 2026-08-04
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Editais ja ingeridos nascem 'novo_captado' (server_default cobre o
    # backfill). Maquina de status completa em licitacoes/triagem.py.
    op.add_column(
        "licitacoes",
        sa.Column(
            "status_triagem",
            sa.String(length=32),
            nullable=False,
            server_default="novo_captado",
        ),
    )
    op.create_index(
        "ix_licitacoes_status_triagem", "licitacoes", ["status_triagem"]
    )

    op.create_table(
        "licitacoes_decisoes_triagem",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 'aprovado' | 'rejeitado' | 'observacao'
        sa.Column("decisao", sa.String(length=16), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("usuario_email", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_licitacoes_decisoes_triagem_licitacao_id",
        "licitacoes_decisoes_triagem",
        ["licitacao_id"],
    )
    op.create_index(
        "ix_decisoes_triagem_lic_criado",
        "licitacoes_decisoes_triagem",
        ["licitacao_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_decisoes_triagem_lic_criado",
        table_name="licitacoes_decisoes_triagem",
    )
    op.drop_index(
        "ix_licitacoes_decisoes_triagem_licitacao_id",
        table_name="licitacoes_decisoes_triagem",
    )
    op.drop_table("licitacoes_decisoes_triagem")
    op.drop_index("ix_licitacoes_status_triagem", table_name="licitacoes")
    op.drop_column("licitacoes", "status_triagem")
