"""dp_onboarding_syncs: historico do push de onboarding (OnSafety, A).

Revision ID: a7b8c9d0e1f2
Revises: ee4f5a6b7c8d
Create Date: 2026-07-12
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "ee4f5a6b7c8d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dp_onboarding_syncs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("dp_employees.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("sistema", sa.String(length=32), nullable=False, index=True),
        sa.Column(
            "correlation_id", sa.String(length=64), nullable=False, index=True
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("error_msg", sa.String(length=500), nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("dp_onboarding_syncs")
