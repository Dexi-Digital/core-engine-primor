"""fiscal_nfe_detalhes

2o passe de extracao da NF-e (Squad 4 / demanda #8 -- migracao
90->TOTVS): UF, flag de DV da chave, totais de impostos e vinculo com
obra em `fiscal_documentos`; nova tabela filha `fiscal_documento_itens`
com os <det> (NCM/CFOP/quantidades/valores).

Nota: o brief original apontava `down_revision = "b8c9d0e1f2a3"`, mas o
head real deste worktree (checado via `alembic heads`) e
`a1b2c3d4e5f6` (feat/captador-triagem, ja mergeado a frente de
b8c9d0e1f2a3). Ajustado para manter o worktree single-headed.

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fiscal_documentos", sa.Column("uf", sa.String(2), nullable=True)
    )
    op.create_index(
        "ix_fiscal_documentos_uf", "fiscal_documentos", ["uf"]
    )
    op.add_column(
        "fiscal_documentos",
        sa.Column("chave_dv_valida", sa.Boolean(), nullable=True),
    )
    for col in ("valor_icms", "valor_ipi", "valor_pis", "valor_cofins"):
        op.add_column(
            "fiscal_documentos",
            sa.Column(col, sa.Numeric(20, 2), nullable=True),
        )
    op.add_column(
        "fiscal_documentos",
        sa.Column("obra_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_fiscal_documentos_obra_id", "fiscal_documentos", ["obra_id"]
    )
    op.create_foreign_key(
        "fk_fiscal_documentos_obra_id",
        "fiscal_documentos",
        "obras_obra",
        ["obra_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "fiscal_documento_itens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "documento_id",
            sa.Integer(),
            sa.ForeignKey("fiscal_documentos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(64), nullable=True),
        sa.Column("descricao", sa.String(512), nullable=True),
        sa.Column("ncm", sa.String(16), nullable=True),
        sa.Column("cfop", sa.String(8), nullable=True),
        sa.Column("unidade", sa.String(16), nullable=True),
        sa.Column("quantidade", sa.Numeric(20, 4), nullable=True),
        sa.Column("valor_unitario", sa.Numeric(20, 10), nullable=True),
        sa.Column("valor_total", sa.Numeric(20, 2), nullable=True),
        sa.UniqueConstraint(
            "documento_id", "ordem", name="uq_fiscal_item_documento_ordem"
        ),
    )
    op.create_index(
        "ix_fiscal_documento_itens_documento_id",
        "fiscal_documento_itens",
        ["documento_id"],
    )


def downgrade() -> None:
    op.drop_table("fiscal_documento_itens")
    op.drop_constraint(
        "fk_fiscal_documentos_obra_id", "fiscal_documentos", type_="foreignkey"
    )
    op.drop_index("ix_fiscal_documentos_obra_id", table_name="fiscal_documentos")
    op.drop_column("fiscal_documentos", "obra_id")
    for col in ("valor_cofins", "valor_pis", "valor_ipi", "valor_icms"):
        op.drop_column("fiscal_documentos", col)
    op.drop_column("fiscal_documentos", "chave_dv_valida")
    op.drop_index("ix_fiscal_documentos_uf", table_name="fiscal_documentos")
    op.drop_column("fiscal_documentos", "uf")
