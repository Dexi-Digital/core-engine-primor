"""dp_employee_documents.obra_id: obra do documento vinda do pull OnSafety.

O `establishment` (Projeto) chega junto nos treinamentos e nas fichas
de EPI da OnSafety. Guardamos o vinculo com `obras_obra` para o dossie
responder "quais NRs deste funcionario sao desta obra". SET NULL na
exclusao da obra -- o documento do funcionario continua valido.

Revision ID: c9d0e1f2a3b4
Revises: e5ef0911decd
Create Date: 2026-09-08
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "e5ef0911decd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dp_employee_documents",
        sa.Column("obra_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_dp_employee_documents_obra_id",
        "dp_employee_documents",
        ["obra_id"],
    )
    op.create_foreign_key(
        "fk_dp_employee_documents_obra_id",
        "dp_employee_documents",
        "obras_obra",
        ["obra_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_dp_employee_documents_obra_id",
        "dp_employee_documents",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_dp_employee_documents_obra_id",
        table_name="dp_employee_documents",
    )
    op.drop_column("dp_employee_documents", "obra_id")
