"""licitacoes: resultados homologados + atas de registro de preco (Squad 3)

Revision ID: e5ef0911decd
Revises: 48db67e9ae44
Create Date: 2026-08-04

Nota: o brief original sugeria o revision id `a1b2c3d4e5f6`, mas esse id ja
esta em uso por `alembic/versions/a1b2c3d4e5f6_captador_triagem.py`
(revision colidente, nao apenas down_revision desatualizado). Gerado um id
novo (`e5ef0911decd`) para manter a cadeia com uma unica head. O
down_revision aponta para `48db67e9ae44`, que e o head real deste worktree
no momento da implementacao -- o main ja avancou para `c7d8e9f0a1b2`
(Squad 4 mergeou); a relinearizacao final (re-apontar esta revision para
`c7d8e9f0a1b2`) acontece no merge, a cargo do controller.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "e5ef0911decd"
down_revision = "48db67e9ae44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "licitacoes_resultados",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_numero", sa.Integer(), nullable=False),
        sa.Column("sequencial_resultado", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cnpj_vencedor", sa.String(length=32), nullable=True),
        sa.Column("razao_social", sa.String(length=512), nullable=True),
        sa.Column("valor_homologado", sa.Numeric(20, 2), nullable=True),
        sa.Column("valor_unitario", sa.Numeric(20, 4), nullable=True),
        sa.Column("quantidade", sa.Numeric(20, 4), nullable=True),
        sa.Column("data_resultado", sa.DateTime(timezone=True), nullable=True),
        sa.Column("situacao", sa.String(length=64), nullable=True),
        sa.Column("porte_fornecedor", sa.String(length=64), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "licitacao_id", "item_numero", "sequencial_resultado",
            name="uq_resultado_item_seq",
        ),
    )
    op.create_index("ix_licitacoes_resultados_licitacao_id", "licitacoes_resultados", ["licitacao_id"])
    op.create_index("ix_licitacoes_resultados_cnpj_vencedor", "licitacoes_resultados", ["cnpj_vencedor"])
    op.create_index("ix_licitacoes_resultados_data_resultado", "licitacoes_resultados", ["data_resultado"])
    op.create_index("ix_resultados_cnpj_data", "licitacoes_resultados", ["cnpj_vencedor", "data_resultado"])

    op.create_table(
        "licitacoes_atas_rp",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("numero_controle_pncp_ata", sa.String(length=128), nullable=False),
        sa.Column("numero_ata", sa.String(length=64), nullable=True),
        sa.Column("ano_ata", sa.Integer(), nullable=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("orgao_cnpj", sa.String(length=32), nullable=True),
        sa.Column("orgao_nome", sa.String(length=512), nullable=True),
        sa.Column("objeto", sa.Text(), nullable=True),
        sa.Column("vigencia_inicio", sa.Date(), nullable=True),
        sa.Column("vigencia_fim", sa.Date(), nullable=True),
        sa.Column("valor", sa.Numeric(20, 2), nullable=True),
        sa.Column("cancelado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("possibilidade_adesao", sa.Boolean(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("numero_controle_pncp_ata", name="uq_ata_numero_controle"),
    )
    op.create_index("ix_licitacoes_atas_rp_licitacao_id", "licitacoes_atas_rp", ["licitacao_id"])
    op.create_index("ix_licitacoes_atas_rp_orgao_cnpj", "licitacoes_atas_rp", ["orgao_cnpj"])
    op.create_index("ix_licitacoes_atas_rp_vigencia_fim", "licitacoes_atas_rp", ["vigencia_fim"])


def downgrade() -> None:
    op.drop_table("licitacoes_atas_rp")
    op.drop_table("licitacoes_resultados")
