"""Estado da carga do EasyJur (em_andamento / ok / erro).

A carga passou a rodar no worker: o botao "Sincronizar agora" bloqueava
a requisicao por ~2,5 min sem feedback, e o erro era engolido. Agora o
log guarda o estado e a tela mostra.

Revision ID: c9e5a13f7b42
Revises: b8d4f02e5a31
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c9e5a13f7b42"
down_revision = "b8d4f02e5a31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "juridico_sync_log",
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
    )
    op.add_column(
        "juridico_sync_log",
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("juridico_sync_log", "iniciado_em")
    op.drop_column("juridico_sync_log", "status")
