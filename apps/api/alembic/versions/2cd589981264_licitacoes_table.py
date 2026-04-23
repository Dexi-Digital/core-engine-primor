"""licitacoes table

Revision ID: 2cd589981264
Revises:
Create Date: 2026-04-23
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "2cd589981264"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource", sa.String(length=255), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "dp_employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cpf", sa.String(length=14), nullable=False),
        sa.Column("nome_completo", sa.String(length=255), nullable=False),
        sa.Column("cargo", sa.String(length=100), nullable=False),
    )
    op.create_index("ix_dp_employees_cpf", "dp_employees", ["cpf"], unique=True)

    op.create_table(
        "licitacoes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="pncp"),
        sa.Column("numero_compra", sa.String(length=64), nullable=True),
        sa.Column("ano_compra", sa.Integer(), nullable=True),
        sa.Column("sequencial_compra", sa.Integer(), nullable=True),
        sa.Column("objeto_compra", sa.String(), nullable=True),
        sa.Column("modalidade_nome", sa.String(length=128), nullable=True),
        sa.Column("modo_disputa_nome", sa.String(length=128), nullable=True),
        sa.Column("situacao_compra_nome", sa.String(length=128), nullable=True),
        sa.Column("valor_total_estimado", sa.Numeric(20, 2), nullable=True),
        sa.Column("valor_total_homologado", sa.Numeric(20, 2), nullable=True),
        sa.Column("srp", sa.Boolean(), nullable=True),
        sa.Column("orgao_cnpj", sa.String(length=32), nullable=True),
        sa.Column("orgao_razao_social", sa.String(length=512), nullable=True),
        sa.Column("uf_sigla", sa.String(length=4), nullable=True),
        sa.Column("municipio_nome", sa.String(length=128), nullable=True),
        sa.Column("codigo_ibge", sa.String(length=16), nullable=True),
        sa.Column("data_publicacao_pncp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_atualizacao_pncp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_licitacoes_external_id", "licitacoes", ["external_id"], unique=True)
    op.create_index("ix_licitacoes_source", "licitacoes", ["source"])
    op.create_index("ix_licitacoes_ano_compra", "licitacoes", ["ano_compra"])
    op.create_index("ix_licitacoes_modalidade_nome", "licitacoes", ["modalidade_nome"])
    op.create_index("ix_licitacoes_orgao_cnpj", "licitacoes", ["orgao_cnpj"])
    op.create_index("ix_licitacoes_uf_sigla", "licitacoes", ["uf_sigla"])
    op.create_index("ix_licitacoes_data_publicacao_pncp", "licitacoes", ["data_publicacao_pncp"])
    op.create_index("ix_licitacoes_uf_modalidade", "licitacoes", ["uf_sigla", "modalidade_nome"])
    op.create_index(
        "ix_licitacoes_publicacao_uf", "licitacoes", ["data_publicacao_pncp", "uf_sigla"]
    )


def downgrade() -> None:
    for idx in (
        "ix_licitacoes_publicacao_uf",
        "ix_licitacoes_uf_modalidade",
        "ix_licitacoes_data_publicacao_pncp",
        "ix_licitacoes_uf_sigla",
        "ix_licitacoes_orgao_cnpj",
        "ix_licitacoes_modalidade_nome",
        "ix_licitacoes_ano_compra",
        "ix_licitacoes_source",
        "ix_licitacoes_external_id",
    ):
        op.drop_index(idx, table_name="licitacoes")
    op.drop_table("licitacoes")
    op.drop_index("ix_dp_employees_cpf", table_name="dp_employees")
    op.drop_table("dp_employees")
    op.drop_table("audit_log")
