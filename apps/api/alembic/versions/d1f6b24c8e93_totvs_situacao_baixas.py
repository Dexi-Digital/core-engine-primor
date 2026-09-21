"""STATUSLAN interpretado + baixas da FLAN (TOTVS, ticket 30268517).

Revision ID: d1f6b24c8e93
Revises: c9e5a13f7b42
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d1f6b24c8e93"
down_revision = "c9e5a13f7b42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("totvs_lancamentos", sa.Column("situacao", sa.String(32), nullable=True))
    op.add_column("totvs_lancamentos", sa.Column("valor_baixado", sa.Numeric(20, 2), nullable=True))
    op.add_column("totvs_lancamentos", sa.Column("saldo", sa.Numeric(20, 2), nullable=True))
    op.create_index("ix_totvs_lancamentos_situacao", "totvs_lancamentos", ["situacao"])


def downgrade() -> None:
    op.drop_index("ix_totvs_lancamentos_situacao", table_name="totvs_lancamentos")
    op.drop_column("totvs_lancamentos", "saldo")
    op.drop_column("totvs_lancamentos", "valor_baixado")
    op.drop_column("totvs_lancamentos", "situacao")
