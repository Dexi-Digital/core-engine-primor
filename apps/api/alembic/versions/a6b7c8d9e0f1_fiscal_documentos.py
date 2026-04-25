"""fiscal_documentos: NF-e/NFS-e/NFC-e/CT-e/CF-e/Baixa para envio Domínio

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-04-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Modulo C - documentos fiscais para envio ao escritorio contabil
    # via API Dominio (Central do Desenvolvedor). 6 tipos: NF-e, NFS-e,
    # NFC-e, CT-e, CF-e, Baixa de Parcela. Status_envio comeca em
    # `pendente`, vai pra `enviado` quando o adapter retorna protocolo,
    # e cai em `erro` se a chamada falhar (worker retenta com backoff).
    op.create_table(
        "fiscal_documentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tipo", sa.String(16), nullable=False, index=True),
        sa.Column("chave_acesso", sa.String(64), nullable=True, index=True),
        sa.Column("numero", sa.String(32), nullable=True),
        sa.Column("serie", sa.String(8), nullable=True),
        sa.Column("emitente_cnpj", sa.String(20), nullable=True, index=True),
        sa.Column("emitente_nome", sa.String(255), nullable=True),
        sa.Column("destinatario_cnpj", sa.String(20), nullable=True, index=True),
        sa.Column("destinatario_nome", sa.String(255), nullable=True),
        sa.Column("valor_total", sa.Numeric(20, 2), nullable=True),
        sa.Column("data_emissao", sa.DateTime(timezone=True), nullable=True),
        sa.Column("xml_path", sa.String(1024), nullable=False),
        sa.Column("xml_hash", sa.String(64), nullable=True, index=True),
        sa.Column(
            "status_envio",
            sa.String(16),
            nullable=False,
            server_default="pendente",
            index=True,
        ),
        sa.Column("protocolo_dominio", sa.String(128), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_msg", sa.String(2000), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("observacoes", sa.String(2000), nullable=True),
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
        # Idempotencia: o mesmo XML (chave_acesso + tipo) nao deve gerar
        # duas linhas. Quando chave_acesso for null (CF-e/Baixa), caimos
        # no xml_hash que nunca eh null.
        sa.UniqueConstraint("tipo", "chave_acesso", name="uq_fiscal_tipo_chave"),
        sa.UniqueConstraint("xml_hash", name="uq_fiscal_xml_hash"),
    )
    op.create_index(
        "ix_fiscal_documentos_emissao_tipo",
        "fiscal_documentos",
        ["data_emissao", "tipo"],
    )


def downgrade() -> None:
    op.drop_index("ix_fiscal_documentos_emissao_tipo", "fiscal_documentos")
    op.drop_table("fiscal_documentos")
