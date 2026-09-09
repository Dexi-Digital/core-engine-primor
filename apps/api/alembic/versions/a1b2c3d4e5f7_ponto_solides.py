"""ponto solides/tangerino

Locais de trabalho, funcionarios, batidas e log de execucao do pull do
ponto eletronico. Desenho derivado da API REAL da conta da Primor
(27/08/2026): 395 funcionarios, 83 locais de trabalho, e os locais SAO
as obras -- o que fecha a pergunta em aberto do ADR-003 sobre o vinculo
funcionario -> obra.

`ponto_sync_log` tem `source` na unique key desde o primeiro commit,
mesmo precedente do TOTVS: disparo manual nao pode queimar a janela do
job agendado.

Revision ID: a1b2c3d4e5f7
Revises: f6a7b8c9d0e1
Create Date: 2026-08-28
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f7"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ponto_locais_trabalho",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tangerino_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=255), nullable=False),
        sa.Column("nome_normalizado", sa.String(length=255), nullable=False, index=True),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("codigo_obra", sa.String(length=32), nullable=True, index=True),
        sa.Column(
            "obra_id",
            sa.Integer(),
            sa.ForeignKey("obras_obra.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_ponto_locais_tangerino_id",
        "ponto_locais_trabalho",
        ["tangerino_id"],
        unique=True,
    )

    op.create_table(
        "ponto_funcionarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tangerino_id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=255), nullable=True),
        sa.Column("cpf", sa.String(length=11), nullable=True, index=True),
        sa.Column("pis", sa.String(length=11), nullable=True),
        sa.Column("data_admissao", sa.Date(), nullable=True),
        sa.Column("demitido", sa.Boolean(), nullable=False, server_default=sa.false(), index=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("dp_employees.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("local_trabalho_externo_id", sa.Integer(), nullable=True, index=True),
        sa.Column("local_trabalho_nome", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_ponto_funcionarios_tangerino_id",
        "ponto_funcionarios",
        ["tangerino_id"],
        unique=True,
    )

    op.create_table(
        "ponto_batidas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("tangerino_employee_id", sa.Integer(), nullable=False, index=True),
        sa.Column(
            "funcionario_id",
            sa.Integer(),
            sa.ForeignKey("ponto_funcionarios.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("data_trabalho", sa.Date(), nullable=True, index=True),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fim", sa.DateTime(timezone=True), nullable=True),
        sa.Column("segundos_trabalhados", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_ponto_batidas_external_id", "ponto_batidas", ["external_id"], unique=True)
    op.create_index(
        "ix_ponto_batidas_func_data", "ponto_batidas", ["funcionario_id", "data_trabalho"]
    )

    op.create_table(
        "ponto_sync_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=16), nullable=False, index=True),
        sa.Column("janela", sa.String(length=48), nullable=False, index=True),
        sa.Column("recurso", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("lidos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gravados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(length=2048), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "source", "janela", "recurso", name="uq_ponto_sync_source_janela_recurso"
        ),
    )


def downgrade() -> None:
    op.drop_table("ponto_sync_log")
    op.drop_index("ix_ponto_batidas_func_data", table_name="ponto_batidas")
    op.drop_index("ix_ponto_batidas_external_id", table_name="ponto_batidas")
    op.drop_table("ponto_batidas")
    op.drop_index("ix_ponto_funcionarios_tangerino_id", table_name="ponto_funcionarios")
    op.drop_table("ponto_funcionarios")
    op.drop_index("ix_ponto_locais_tangerino_id", table_name="ponto_locais_trabalho")
    op.drop_table("ponto_locais_trabalho")
