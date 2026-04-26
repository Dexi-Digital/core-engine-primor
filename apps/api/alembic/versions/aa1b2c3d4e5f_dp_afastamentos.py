"""dp_afastamentos: acompanhamento de INSS para afastados (D4).

Revision ID: aa1b2c3d4e5f
Revises: f1a2b3c4d5e6
Create Date: 2026-04-26
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "aa1b2c3d4e5f"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dp_afastamentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("dp_employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "beneficio_tipo",
            sa.String(length=16),
            nullable=False,
            server_default="B31",
        ),
        sa.Column("numero_beneficio", sa.String(length=32), nullable=True),
        sa.Column("cid", sa.String(length=16), nullable=True),
        sa.Column("data_inicio", sa.Date(), nullable=False),
        sa.Column("dcb", sa.Date(), nullable=True),
        sa.Column("data_pericia", sa.Date(), nullable=True),
        sa.Column("data_retorno", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="em_andamento",
        ),
        sa.Column("observacoes", sa.Text(), nullable=True),
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
    op.create_index(
        "ix_dp_afastamentos_employee_id",
        "dp_afastamentos",
        ["employee_id"],
    )
    op.create_index("ix_dp_afastamentos_dcb", "dp_afastamentos", ["dcb"])
    op.create_index(
        "ix_dp_afastamentos_data_pericia",
        "dp_afastamentos",
        ["data_pericia"],
    )
    op.create_index(
        "ix_dp_afastamentos_status", "dp_afastamentos", ["status"]
    )

    op.create_table(
        "dp_afastamentos_alertas_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "afastamento_id",
            sa.Integer(),
            sa.ForeignKey("dp_afastamentos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
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
            "afastamento_id",
            "kind",
            "janela",
            name="uq_afastamento_alerta_kind_janela",
        ),
    )
    op.create_index(
        "ix_dp_afastamentos_alertas_log_afastamento_id",
        "dp_afastamentos_alertas_log",
        ["afastamento_id"],
    )


def downgrade() -> None:
    op.drop_table("dp_afastamentos_alertas_log")
    op.drop_table("dp_afastamentos")
