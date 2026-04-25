"""dp_employees: expand schema + empregos_anteriores + dossie_consultas

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-04-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- expand dp_employees ---------------------------------------------
    with op.batch_alter_table("dp_employees") as batch:
        batch.add_column(sa.Column("matricula", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("nome_social", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("rg", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("rg_orgao_emissor", sa.String(length=32), nullable=True)
        )
        batch.add_column(sa.Column("pis_pasep", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("ctps_numero", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("ctps_serie", sa.String(length=16), nullable=True))
        batch.add_column(
            sa.Column("titulo_eleitor", sa.String(length=20), nullable=True)
        )
        batch.add_column(sa.Column("cnh_numero", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("cnh_categoria", sa.String(length=8), nullable=True))
        batch.add_column(sa.Column("cnh_validade", sa.Date(), nullable=True))

        batch.add_column(sa.Column("data_nascimento", sa.Date(), nullable=True))
        batch.add_column(sa.Column("sexo", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("estado_civil", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("escolaridade", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("nome_mae", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("nome_pai", sa.String(length=255), nullable=True))
        batch.add_column(
            sa.Column(
                "nacionalidade",
                sa.String(length=64),
                nullable=True,
                server_default="Brasileira",
            )
        )

        batch.add_column(sa.Column("telefone", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("email", sa.String(length=255), nullable=True))

        batch.add_column(sa.Column("cep", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("logradouro", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("numero", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("complemento", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("bairro", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("cidade", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("uf", sa.String(length=2), nullable=True))

        batch.add_column(sa.Column("obra", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("setor", sa.String(length=128), nullable=True))
        batch.add_column(
            sa.Column("tipo_contrato", sa.String(length=32), nullable=True)
        )
        batch.add_column(sa.Column("salario_base", sa.Numeric(12, 2), nullable=True))
        batch.add_column(sa.Column("data_admissao", sa.Date(), nullable=True))
        batch.add_column(sa.Column("data_desligamento", sa.Date(), nullable=True))
        batch.add_column(
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="ativo",
            )
        )

        batch.add_column(sa.Column("aso_data", sa.Date(), nullable=True))
        batch.add_column(sa.Column("aso_validade", sa.Date(), nullable=True))
        batch.add_column(sa.Column("aso_resultado", sa.String(length=16), nullable=True))

        batch.add_column(
            sa.Column(
                "source",
                sa.String(length=32),
                nullable=False,
                server_default="manual",
            )
        )
        batch.add_column(sa.Column("observacoes", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )

    op.create_index(
        "ix_dp_employees_matricula", "dp_employees", ["matricula"], unique=False
    )
    op.create_index(
        "ix_dp_employees_status", "dp_employees", ["status"], unique=False
    )

    # --- empregos_anteriores --------------------------------------------
    op.create_table(
        "dp_empregos_anteriores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("dp_employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("empresa_cnpj", sa.String(length=20), nullable=True),
        sa.Column("empresa_razao_social", sa.String(length=255), nullable=True),
        sa.Column("cargo", sa.String(length=128), nullable=True),
        sa.Column("inicio", sa.Date(), nullable=True),
        sa.Column("fim", sa.Date(), nullable=True),
        sa.Column("motivo_desligamento", sa.String(length=64), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_dp_empregos_anteriores_employee_id",
        "dp_empregos_anteriores",
        ["employee_id"],
    )

    # --- dossie_consultas (LGPD audit) -----------------------------------
    op.create_table(
        "dp_dossie_consultas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("dp_employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fonte", sa.String(length=32), nullable=False),
        sa.Column("chave_consulta", sa.String(length=32), nullable=False),
        sa.Column(
            "sucesso", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_dp_dossie_consultas_employee_id",
        "dp_dossie_consultas",
        ["employee_id"],
    )
    op.create_index(
        "ix_dp_dossie_consultas_fonte", "dp_dossie_consultas", ["fonte"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dp_dossie_consultas_fonte", table_name="dp_dossie_consultas"
    )
    op.drop_index(
        "ix_dp_dossie_consultas_employee_id", table_name="dp_dossie_consultas"
    )
    op.drop_table("dp_dossie_consultas")

    op.drop_index(
        "ix_dp_empregos_anteriores_employee_id",
        table_name="dp_empregos_anteriores",
    )
    op.drop_table("dp_empregos_anteriores")

    op.drop_index("ix_dp_employees_status", table_name="dp_employees")
    op.drop_index("ix_dp_employees_matricula", table_name="dp_employees")

    with op.batch_alter_table("dp_employees") as batch:
        for col in (
            "updated_at",
            "created_at",
            "observacoes",
            "source",
            "aso_resultado",
            "aso_validade",
            "aso_data",
            "status",
            "data_desligamento",
            "data_admissao",
            "salario_base",
            "tipo_contrato",
            "setor",
            "obra",
            "uf",
            "cidade",
            "bairro",
            "complemento",
            "numero",
            "logradouro",
            "cep",
            "email",
            "telefone",
            "nacionalidade",
            "nome_pai",
            "nome_mae",
            "escolaridade",
            "estado_civil",
            "sexo",
            "data_nascimento",
            "cnh_validade",
            "cnh_categoria",
            "cnh_numero",
            "titulo_eleitor",
            "ctps_serie",
            "ctps_numero",
            "pis_pasep",
            "rg_orgao_emissor",
            "rg",
            "nome_social",
            "matricula",
        ):
            batch.drop_column(col)
