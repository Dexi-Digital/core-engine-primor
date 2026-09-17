"""Jornada de admissao -- etapa, kit e rastro de entrega.

A tabela que faltava para o Motor Central CONDUZIR a admissao em vez de
so guardar o cadastro. Ver `app.modules.dp_sesmt.admissao`.

Revision ID: 523a1b36b275
Revises: d0e1f2a3b4c5
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "523a1b36b275"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dp_admissao_jornadas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("dp_employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("obra", sa.String(length=128), nullable=True),
        sa.Column(
            "etapa",
            sa.String(length=32),
            nullable=False,
            server_default="rascunho",
        ),
        sa.Column("kit_path", sa.String(length=1024), nullable=True),
        sa.Column("kit_gerado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kit_entregue_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kit_entregue_para", sa.String(length=255), nullable=True),
        sa.Column("confirmado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmado_por", sa.String(length=255), nullable=True),
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
    # Unico: admitir a mesma pessoa duas vezes e erro de operacao, e o
    # banco deve recusar em vez de a aplicacao torcer para nao acontecer.
    op.create_index(
        "ix_dp_admissao_jornadas_employee_id",
        "dp_admissao_jornadas",
        ["employee_id"],
        unique=True,
    )
    # Filtro do lote por obra e do painel por etapa -- os dois caminhos
    # que a tela usa.
    op.create_index(
        "ix_dp_admissao_jornadas_obra", "dp_admissao_jornadas", ["obra"]
    )
    op.create_index(
        "ix_dp_admissao_jornadas_etapa", "dp_admissao_jornadas", ["etapa"]
    )


def downgrade() -> None:
    op.drop_index("ix_dp_admissao_jornadas_etapa", "dp_admissao_jornadas")
    op.drop_index("ix_dp_admissao_jornadas_obra", "dp_admissao_jornadas")
    op.drop_index(
        "ix_dp_admissao_jornadas_employee_id", "dp_admissao_jornadas"
    )
    op.drop_table("dp_admissao_jornadas")
