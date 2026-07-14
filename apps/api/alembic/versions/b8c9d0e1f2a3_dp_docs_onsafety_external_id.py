"""dp_employee_documents.onsafety_external_id: idempotencia do pull de EPIs.

Id do `controle_epi` na OnSafety quando a row veio do pull (Squad 2).
Unique parcial (`IS NOT NULL`) permite rows manuais (sem external id)
convivendo com rows do sync -- mesmo padrao de `onedrive_item_id`.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

_TABLE = "dp_employee_documents"
_INDEX = "uq_dp_employee_documents_onsafety_external_id"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("onsafety_external_id", sa.String(64), nullable=True),
    )
    op.create_index(
        _INDEX,
        _TABLE,
        ["onsafety_external_id"],
        unique=True,
        postgresql_where=sa.text("onsafety_external_id IS NOT NULL"),
        sqlite_where=sa.text("onsafety_external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, "onsafety_external_id")
