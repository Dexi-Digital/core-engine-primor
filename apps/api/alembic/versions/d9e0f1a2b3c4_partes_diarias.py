"""partes_diarias: log diario de equipamento + OCR (B.2)

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-04-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Modulo B.2 -- parte diaria com OCR via Google Document AI.
    # Diferente das consultas Detran (B.3), esta tabela e mutavel:
    # o operador revisa os campos extraidos do OCR e pode corrigir
    # antes de fechar o documento. `ocr_payload` guarda a resposta
    # bruta (texto + entities + confidence) para reprocesso e auditoria.
    op.create_table(
        "partes_diarias",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Anexo (PDF/JPG/PNG da parte diaria escaneada). Caminho
        # relativo dentro do `EditaisStorage` (local em dev, OneDrive
        # em prod).
        sa.Column("anexo_path", sa.String(length=1024), nullable=True),
        sa.Column(
            "filename_original", sa.String(length=512), nullable=True
        ),
        sa.Column(
            "mime_type", sa.String(length=64), nullable=True
        ),
        # Campos extraidos do OCR (nullable -- sao revisaveis pelo
        # operador). Indices para filtro/agregacao por data e veiculo.
        sa.Column("data", sa.Date(), nullable=True, index=True),
        sa.Column(
            "veiculo_id",
            sa.Integer(),
            sa.ForeignKey("frota_veiculos.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("operador", sa.String(length=200), nullable=True),
        sa.Column("obra", sa.String(length=200), nullable=True, index=True),
        sa.Column("equipamento", sa.String(length=200), nullable=True),
        sa.Column("placa", sa.String(length=8), nullable=True),
        sa.Column("horimetro_inicio", sa.Numeric(10, 2), nullable=True),
        sa.Column("horimetro_fim", sa.Numeric(10, 2), nullable=True),
        sa.Column("km_inicio", sa.Integer(), nullable=True),
        sa.Column("km_fim", sa.Integer(), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        # Status do OCR -- `pendente` (upload feito mas worker nao
        # processou ainda), `processado` (campos extraidos), `revisado`
        # (operador conferiu/corrigiu), `erro` (Document AI falhou).
        sa.Column(
            "ocr_status",
            sa.String(length=16),
            nullable=False,
            server_default="pendente",
            index=True,
        ),
        sa.Column(
            "ocr_source",
            sa.String(length=32),
            nullable=False,
            server_default="google_documentai_mock",
        ),
        sa.Column("ocr_confidence", sa.Numeric(5, 4), nullable=True),
        # Resposta normalizada do adapter -- `{raw_text, fields,
        # confidence, raw_response, source}`. Ficou em JSON (nao JSONB)
        # para compat com SQLite nos testes.
        sa.Column("ocr_payload", sa.JSON(), nullable=True),
        sa.Column("ocr_error_msg", sa.Text(), nullable=True),
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
    # Indice composto: relatorios por veiculo + data sao a query mais
    # comum (ex.: "ultimos 30 dias da placa ABC1234"). Postgres faz
    # range scan eficiente; SQLite (testes) tambem aproveita.
    op.create_index(
        "ix_partes_diarias_veiculo_data",
        "partes_diarias",
        ["veiculo_id", "data"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_partes_diarias_veiculo_data", table_name="partes_diarias"
    )
    op.drop_table("partes_diarias")
