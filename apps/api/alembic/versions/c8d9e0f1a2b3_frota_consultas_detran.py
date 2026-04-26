"""frota_consultas_detran: log de consultas Infosimples por veiculo (B.3)

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-04-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Modulo B.3 -- log de consultas Detran via Infosimples (SP/MG/GO).
    # Diferente de `frota_documentos`, esta tabela e *historica*: cada
    # consulta gera uma row independente, mesmo pra mesma placa. Isso
    # permite (a) auditar quanto gastamos em consultas pagas, (b) ver
    # progressao de multas/debitos no tempo, (c) reprocessar payloads
    # antigos sem precisar bater novamente na API.
    #
    # `payload` guarda a resposta normalizada (nao a raw da
    # Infosimples) -- o objetivo e que a UI possa renderizar mesmo
    # se a API mudar de schema. `raw` da resposta original fica em
    # `metadata_json` se precisarmos pra debug.
    op.create_table(
        "frota_consultas_detran",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "veiculo_id",
            sa.Integer(),
            sa.ForeignKey("frota_veiculos.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("placa", sa.String(length=8), nullable=False, index=True),
        sa.Column("uf", sa.String(length=2), nullable=False, index=True),
        # 'pendente' (worker fila) | 'ok' | 'erro' | 'mock'
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pendente",
            index=True,
        ),
        # 'infosimples' | 'infosimples_mock' | (futuro: 'directdata')
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="infosimples_mock",
        ),
        # JSON generico (SQLite + Postgres) -- mesmo padrao de
        # licitacoes_editais_analises. Em prod o backend Postgres usa
        # JSONB nativamente sem alteracao no codigo Python.
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Indice composto: a UI mais comum e "ultima consulta de uma placa
    # em dado UF". Composite com `executed_at` evita filesort sob lista
    # paginada por placa+uf. Mantemos sem DESC explicito porque SQLite
    # (usado em testes) nao suporta o token, e Postgres faz scan
    # bidirectional do mesmo b-tree de qualquer jeito.
    op.create_index(
        "ix_consultas_detran_placa_uf_executed",
        "frota_consultas_detran",
        ["placa", "uf", "executed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_consultas_detran_placa_uf_executed",
        table_name="frota_consultas_detran",
    )
    op.drop_table("frota_consultas_detran")
