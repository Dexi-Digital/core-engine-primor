"""D1 fase 2 -- sync de docs via OneDrive

Revision ID: dd3e4f5a6b7c
Revises: cc3d4e5f6a7b
Create Date: 2026-04-23

Cria tabela `onedrive_sync_runs` (audit log de execucoes) e adiciona
4 colunas nullable em cada uma das 4 tabelas de documentos
(`dp_employee_documents`, `frota_documentos`, `obras_documentos`,
`empresa_documentos`) pra amarrar cada doc a um item do OneDrive:

    - `onedrive_item_id`       : id do item no drive (idempotencia)
    - `onedrive_path`          : path absoluto ate o item (debug/UI)
    - `onedrive_last_modified` : timestamp do Graph (detectar mudanca)
    - `onedrive_sync_run_id`   : FK para o run que criou/atualizou a row

`onedrive_item_id` e indexado com unique parcial (`IS NOT NULL`) pra
permitir rows manuais (sem item_id) convivendo com rows vindas do
sync -- mesmo padrao usado em `partes_diarias.client_uuid`.

Sem backfill: os doc rows existentes ficam com os 4 campos NULL
(source='manual' continua distinguindo origem manual vs sync).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "dd3e4f5a6b7c"
down_revision = "cc3d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tabelas que ganham os 4 campos. Mantidas aqui (nao importadas dos
# models) pra migration ficar self-contained se o schema evoluir.
_DOC_TABLES = (
    "dp_employee_documents",
    "frota_documentos",
    "obras_documentos",
    "empresa_documentos",
)


def upgrade() -> None:
    # --- Run table --------------------------------------------------------
    op.create_table(
        "onedrive_sync_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="running",
        ),
        sa.Column("scope", sa.String(32), nullable=True),
        sa.Column("triggered_by", sa.String(255), nullable=True),
        sa.Column("root_folder", sa.String(512), nullable=True),
        sa.Column(
            "files_scanned",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "docs_created",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "docs_updated",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "docs_skipped",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "errors_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column("summary_json", sa.JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )

    # --- Colunas nas tabelas de documentos --------------------------------
    for table in _DOC_TABLES:
        op.add_column(
            table,
            sa.Column("onedrive_item_id", sa.String(128), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("onedrive_path", sa.String(1024), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(
                "onedrive_last_modified",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "onedrive_sync_run_id",
                sa.Integer,
                sa.ForeignKey(
                    "onedrive_sync_runs.id", ondelete="SET NULL"
                ),
                nullable=True,
            ),
        )
        # Unique parcial em `onedrive_item_id IS NOT NULL`: garante
        # idempotencia (mesmo item_id != 2 rows) sem bloquear rows
        # manuais (item_id=NULL).
        op.create_index(
            f"ix_{table}_onedrive_item_id",
            table,
            ["onedrive_item_id"],
            unique=True,
            postgresql_where=sa.text("onedrive_item_id IS NOT NULL"),
        )


def downgrade() -> None:
    for table in _DOC_TABLES:
        op.drop_index(f"ix_{table}_onedrive_item_id", table_name=table)
        op.drop_column(table, "onedrive_sync_run_id")
        op.drop_column(table, "onedrive_last_modified")
        op.drop_column(table, "onedrive_path")
        op.drop_column(table, "onedrive_item_id")
    op.drop_table("onedrive_sync_runs")
