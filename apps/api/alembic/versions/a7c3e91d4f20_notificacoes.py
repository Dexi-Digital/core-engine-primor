"""Notificacoes na plataforma (substituem o email dos boletins).

Todo aviso do sistema saia por email (Resend). O cliente pediu que o
boletim de licitacoes virasse notificacao dentro da plataforma, e nao
havia peca de notificacao no projeto -- esta tabela e ela, generica
desde o inicio para certidoes, ASO e afastamentos reusarem.

Revision ID: a7c3e91d4f20
Revises: 9af3996f29a6
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a7c3e91d4f20"
down_revision = "9af3996f29a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notificacoes",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Email, nao FK para auth_users: os despachos atuais trabalham
        # com lista de emails e nem todo destinatario tem conta.
        sa.Column("destinatario", sa.String(length=255), nullable=False),
        sa.Column("categoria", sa.String(length=32), nullable=False),
        sa.Column("titulo", sa.String(length=255), nullable=False),
        sa.Column("corpo", sa.Text(), nullable=False),
        sa.Column("link", sa.String(length=512), nullable=True),
        # Instante, nao booleano: responde "o aviso chegou a tempo?".
        sa.Column("lida_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chave_idempotencia", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_notificacoes_destinatario", "notificacoes", ["destinatario"]
    )
    op.create_index("ix_notificacoes_categoria", "notificacoes", ["categoria"])
    # Consulta quente do sino: nao lidas de uma pessoa, recentes primeiro.
    op.create_index(
        "ix_notificacoes_caixa",
        "notificacoes",
        ["destinatario", "lida_em", "created_at"],
    )
    # Idempotencia POR DESTINATARIO: o mesmo boletim vai para varias
    # pessoas e cada uma precisa da sua copia, mas o beat rodando 3x/dia
    # nao pode gerar tres copias para a mesma pessoa.
    op.create_unique_constraint(
        "uq_notificacoes_destinatario_chave",
        "notificacoes",
        ["destinatario", "chave_idempotencia"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_notificacoes_destinatario_chave", "notificacoes", type_="unique"
    )
    op.drop_index("ix_notificacoes_caixa", table_name="notificacoes")
    op.drop_index("ix_notificacoes_categoria", table_name="notificacoes")
    op.drop_index("ix_notificacoes_destinatario", table_name="notificacoes")
    op.drop_table("notificacoes")
