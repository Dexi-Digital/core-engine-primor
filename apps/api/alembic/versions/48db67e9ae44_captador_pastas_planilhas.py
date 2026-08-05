"""captador: pastas de projeto + planilhas orcamentarias

Revision ID: 48db67e9ae44
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04

Nota: o brief original sugeria o revision id `a7b8c9d0e1f2`, mas esse id ja
esta em uso por `alembic/versions/a7b8c9d0e1f2_dp_onboarding_syncs.py`
(revision colidente, nao apenas down_revision desatualizado). Gerado um id
novo para manter a cadeia com uma unica head.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "48db67e9ae44"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "licitacoes_pastas_projeto",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nome_pasta", sa.String(length=255), nullable=False),
        sa.Column("caminho", sa.String(length=1024), nullable=False),
        sa.Column("link_pasta", sa.String(length=1024), nullable=True),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="criada"
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
        "ix_licitacoes_pastas_projeto_licitacao_id",
        "licitacoes_pastas_projeto",
        ["licitacao_id"],
        unique=True,
    )

    op.create_table(
        "licitacoes_planilhas_orcamentarias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "anexo_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes_editais_anexos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nome_arquivo", sa.String(length=255), nullable=False),
        sa.Column("extensao", sa.String(length=16), nullable=False),
        sa.Column(
            "score_classificacao", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("link", sa.String(length=1024), nullable=True),
        sa.Column(
            "status_validacao",
            sa.String(length=32),
            nullable=False,
            server_default="automatica",
        ),
        sa.Column(
            "principal", sa.Boolean(), nullable=False, server_default=sa.false()
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
        "ix_licitacoes_planilhas_orcamentarias_licitacao_id",
        "licitacoes_planilhas_orcamentarias",
        ["licitacao_id"],
    )
    op.create_index(
        "ix_licitacoes_planilhas_orcamentarias_anexo_id",
        "licitacoes_planilhas_orcamentarias",
        ["anexo_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_licitacoes_planilhas_orcamentarias_anexo_id",
        table_name="licitacoes_planilhas_orcamentarias",
    )
    op.drop_index(
        "ix_licitacoes_planilhas_orcamentarias_licitacao_id",
        table_name="licitacoes_planilhas_orcamentarias",
    )
    op.drop_table("licitacoes_planilhas_orcamentarias")
    op.drop_table("licitacoes_pastas_projeto")
