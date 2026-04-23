"""licitacoes_editais + licitacoes_editais_anexos

Tabelas de D.4 (download de editais). `editais` guarda o status agregado
por licitacao (1-1) e a origem (pncp / comprasnet / licitacoes_e);
`editais_anexos` guarda cada arquivo baixado (edital em si + projetos
+ termos de referencia etc) com o caminho opaco no storage (local FS em
dev, MinIO em prod) e metadata suficiente para re-serv ir download.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-04-23
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "licitacoes_editais",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "licitacao_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="pncp"),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="pending"
        ),
        sa.Column("anexos_count", sa.Integer(), nullable=False, server_default="0"),
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
        "ix_licitacoes_editais_status", "licitacoes_editais", ["status"]
    )

    op.create_table(
        "licitacoes_editais_anexos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "edital_id",
            sa.Integer(),
            sa.ForeignKey("licitacoes_editais.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequencial_documento", sa.Integer(), nullable=False),
        sa.Column("titulo", sa.String(length=512), nullable=True),
        sa.Column("tipo_documento", sa.String(length=128), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("edital_id", "sequencial_documento", name="uq_anexo_seq"),
    )
    op.create_index(
        "ix_anexos_edital_id", "licitacoes_editais_anexos", ["edital_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_anexos_edital_id", table_name="licitacoes_editais_anexos")
    op.drop_table("licitacoes_editais_anexos")
    op.drop_index("ix_licitacoes_editais_status", table_name="licitacoes_editais")
    op.drop_table("licitacoes_editais")
