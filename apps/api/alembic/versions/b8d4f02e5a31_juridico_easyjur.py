"""Contencioso juridico espelhado do EasyJur.

Tres tabelas: processos (453 na base real), andamentos (~12 mil) e o
log de sync com `source` na chave -- mesmo formato do modulo ponto.

Revision ID: b8d4f02e5a31
Revises: a7c3e91d4f20
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b8d4f02e5a31"
down_revision = "a7c3e91d4f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "juridico_processos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("easyjur_id", sa.Integer(), nullable=False),
        sa.Column("numero_cnj", sa.String(32), nullable=True),
        sa.Column("status", sa.String(64), nullable=True),
        sa.Column("area", sa.String(64), nullable=True),
        sa.Column("tribunal", sa.String(32), nullable=True),
        sa.Column("instancia", sa.String(32), nullable=True),
        sa.Column("comarca", sa.String(128), nullable=True),
        sa.Column("titulo", sa.String(255), nullable=True),
        sa.Column("cliente", sa.String(255), nullable=True),
        sa.Column("contrario", sa.String(255), nullable=True),
        # Esparsos na base real (15% / 11% / 6% / 8%): None = nao preenchido.
        sa.Column("tipo_acao", sa.String(255), nullable=True),
        sa.Column("risco", sa.String(64), nullable=True),
        sa.Column("fase_atual", sa.String(128), nullable=True),
        sa.Column("resultado", sa.String(128), nullable=True),
        sa.Column("grupos", sa.Text(), nullable=True),
        sa.Column("codigo_obra", sa.String(32), nullable=True),
        sa.Column(
            "obra_id",
            sa.Integer(),
            sa.ForeignKey("obras_obra.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "sincronizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_juridico_processos_easyjur_id", "juridico_processos", ["easyjur_id"], unique=True
    )
    for col in ("numero_cnj", "status", "area", "tribunal", "codigo_obra", "obra_id"):
        op.create_index(f"ix_juridico_processos_{col}", "juridico_processos", [col])

    op.create_table(
        "juridico_andamentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("easyjur_id", sa.Integer(), nullable=False),
        sa.Column("numero_cnj", sa.String(32), nullable=False),
        sa.Column(
            "processo_id",
            sa.Integer(),
            sa.ForeignKey("juridico_processos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tipo", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("data", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_juridico_andamentos_easyjur_id", "juridico_andamentos", ["easyjur_id"], unique=True
    )
    for col in ("numero_cnj", "processo_id", "status", "data"):
        op.create_index(f"ix_juridico_andamentos_{col}", "juridico_andamentos", [col])
    op.create_index("ix_juridico_andamentos_feed", "juridico_andamentos", ["data", "id"])

    op.create_table(
        "juridico_sync_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("janela", sa.Date(), nullable=False),
        sa.Column("processos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("andamentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_declarado", sa.Integer(), nullable=True),
        sa.Column("divergencia", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("erro", sa.String(1024), nullable=True),
        sa.Column(
            "executado_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # `source` na chave: "sincronizar agora" manual nao queima a
        # janela do job agendado.
        sa.UniqueConstraint("source", "janela", name="uq_juridico_sync_source_janela"),
    )


def downgrade() -> None:
    op.drop_table("juridico_sync_log")
    op.drop_table("juridico_andamentos")
    op.drop_table("juridico_processos")
