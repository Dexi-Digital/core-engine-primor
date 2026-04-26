"""dp_aso_alertas_log: idempotencia do cron diario de ASO (A.2).

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-04-26
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dp_aso_alertas_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("dp_employees.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("janela", sa.String(length=32), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column(
            "resend_message_id", sa.String(length=128), nullable=True
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="sent",
        ),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.UniqueConstraint(
            "employee_id", "janela", name="uq_aso_alerta_employee_janela"
        ),
    )


def downgrade() -> None:
    op.drop_table("dp_aso_alertas_log")
