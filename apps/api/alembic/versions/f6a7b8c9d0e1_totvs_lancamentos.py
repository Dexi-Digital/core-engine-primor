"""totvs lancamentos + sync log

Tabelas de destino do pull do TOTVS RM (Modulo C):

- `totvs_lancamentos`: unique em `external_id` SOZINHO (chave natural
  do RM `codcoligada-codfilial-idlan`), igual ao padrao do PNCP. O
  caminho de extracao (`extractor`) e proveniencia, nao chave -- senao
  trocar REST por wsConsultaSQL duplicaria a base inteira.
- `totvs_sync_log`: unique em (`source`, `janela`), com `source` na
  chave DESDE O PRIMEIRO COMMIT. E o bug do
  `dispatch_contrato_alerts_endpoint`: em `contratos_alertas_log` a
  unique key nao distingue origem, entao disparo manual queima a janela
  do beat. Aqui "beat" e "manual" ocupam linhas distintas.

Revision ID: f6a7b8c9d0e1
Revises: e5ef0911decd
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5ef0911decd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "totvs_lancamentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("codcoligada", sa.Integer(), nullable=True, index=True),
        sa.Column("codfilial", sa.Integer(), nullable=True),
        sa.Column("idlan", sa.Integer(), nullable=True),
        sa.Column("valor", sa.Numeric(20, 2), nullable=True),
        sa.Column("contraparte_documento", sa.String(length=32), nullable=True, index=True),
        sa.Column("contraparte_nome", sa.String(length=255), nullable=True),
        sa.Column("data_vencimento", sa.Date(), nullable=True, index=True),
        sa.Column("data_emissao", sa.Date(), nullable=True),
        sa.Column("status_rm", sa.String(length=32), nullable=True),
        sa.Column("extractor", sa.String(length=32), nullable=False, index=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_totvs_lancamentos_external_id", "totvs_lancamentos", ["external_id"], unique=True
    )
    op.create_index(
        "ix_totvs_lancamentos_doc_venc",
        "totvs_lancamentos",
        ["contraparte_documento", "data_vencimento"],
    )

    op.create_table(
        "totvs_sync_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=16), nullable=False, index=True),
        sa.Column("janela", sa.String(length=32), nullable=False, index=True),
        sa.Column("extractor", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("lidos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gravados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(length=2048), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("source", "janela", name="uq_totvs_sync_source_janela"),
    )


def downgrade() -> None:
    op.drop_table("totvs_sync_log")
    op.drop_index("ix_totvs_lancamentos_doc_venc", table_name="totvs_lancamentos")
    op.drop_index("ix_totvs_lancamentos_external_id", table_name="totvs_lancamentos")
    op.drop_table("totvs_lancamentos")
