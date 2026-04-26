"""D1 -- Diagnostico documental: obras, docs por funcionario/empresa/obra,
flags SST em dp_employees, e tabelas do motor de diagnostico.

Cobre Demanda 1 do briefing (mapeamento ZAG/PRIMOR fornecido pela
Lorrayne):

- `obras_obra` (codigo, nome, uf, cidade, status, encerramento_previsto)
- `obras_documentos` (ART/RRT, alvara, PCMAT, CIPA, RNC, RIA, medicoes,
  diario de obra, checklist de alojamento/vivencia)
- `dp_employee_documents` (NR-10/12/18/35, exame toxicologico, ordem de
  servico, lista de integracao, ficha EPI, termo LGPD, contrato
  experiencia, acordo compensacao, RCT, PPP)
- `empresa_documentos` (contrato social, alteracoes contratuais,
  balanco patrimonial, SICAF, CAGEF, SUCAF). Separado de
  `certidoes_empresa` que e so CND/atestado/ART.
- `diagnostico_runs` + `diagnostico_findings` (snapshot do run + cada
  item conformidade)
- `dp_employees`: novas flags booleanas (`is_motorista`,
  `is_operador_maquina`, `is_admin_office`, `is_alturas`,
  `is_eletricista`) -- disparam regras condicionais no checklist SST.

Note: `frota_documentos.tipo` e `certidoes_empresa.tipo` ja sao String
livre, entao novos tipos (AET, CIV, CTPP, RAMU, etc.) nao precisam de
migration -- entram como constantes no codigo.

Revision ID: bb2c3d4e5f6a
Revises: aa1b2c3d4e5f
Create Date: 2026-04-26
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "bb2c3d4e5f6a"
down_revision = "aa1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. obras --------------------------------------------------------
    op.create_table(
        "obras_obra",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codigo", sa.String(32), nullable=False, index=True),
        sa.Column("nome", sa.String(255), nullable=False),
        sa.Column("cliente", sa.String(255), nullable=True),
        sa.Column("uf", sa.String(2), nullable=True, index=True),
        sa.Column("cidade", sa.String(128), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="ativa",
            index=True,
        ),  # ativa | encerrada | suspensa
        sa.Column("data_inicio", sa.Date(), nullable=True),
        sa.Column("encerramento_previsto", sa.Date(), nullable=True),
        sa.Column("data_encerramento", sa.Date(), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("codigo", name="uq_obras_obra_codigo"),
    )

    op.create_table(
        "obras_documentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "obra_id",
            sa.Integer(),
            sa.ForeignKey("obras_obra.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tipo", sa.String(64), nullable=False, index=True),
        sa.Column("numero", sa.String(128), nullable=True),
        sa.Column("emissao", sa.Date(), nullable=True),
        sa.Column("validade", sa.Date(), nullable=True, index=True),
        sa.Column("orgao_emissor", sa.String(255), nullable=True),
        sa.Column("anexo_path", sa.String(1024), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # --- 2. dp_employee_documents ---------------------------------------
    # Generic doc store for funcionarios. ASO continua flat em
    # `dp_employees` (compat com A.2 ja em prod). Novos tipos (NR-10/
    # 12/18/35, toxicologico, OS, integracao, ficha EPI, termo LGPD,
    # contrato experiencia, acordo compensacao, RCT, PPP) entram aqui.
    op.create_table(
        "dp_employee_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("dp_employees.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tipo", sa.String(64), nullable=False, index=True),
        sa.Column("numero", sa.String(128), nullable=True),
        sa.Column("emissao", sa.Date(), nullable=True),
        sa.Column("validade", sa.Date(), nullable=True, index=True),
        sa.Column("anexo_path", sa.String(1024), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_dp_employee_documents_emp_tipo",
        "dp_employee_documents",
        ["employee_id", "tipo"],
    )

    # --- 3. dp_employees: flags SST -------------------------------------
    # Boolean flags determinam regras condicionais no checklist SST:
    # - is_motorista -> exige exame toxicologico
    # - is_operador_maquina -> exige NR-12
    # - is_alturas -> exige NR-35
    # - is_eletricista -> exige NR-10
    # - is_admin_office -> aliviar (nao exige NR-18 todas as vezes)
    op.add_column(
        "dp_employees",
        sa.Column(
            "is_motorista",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "dp_employees",
        sa.Column(
            "is_operador_maquina",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "dp_employees",
        sa.Column(
            "is_admin_office",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "dp_employees",
        sa.Column(
            "is_alturas",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "dp_employees",
        sa.Column(
            "is_eletricista",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # --- 4. empresa_documentos ------------------------------------------
    # Documentos societarios e cadastros oficiais. `certidoes_empresa`
    # continua dedicada a CNDs/atestados (com vencimento curto + alerta
    # 30/15/7/0). Aqui ficam docs perenes ou de longa validade
    # (contrato social, balanco patrimonial, SICAF, CAGEF, SUCAF).
    op.create_table(
        "empresa_documentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("empresa_cnpj", sa.String(20), nullable=False, index=True),
        sa.Column("tipo", sa.String(64), nullable=False, index=True),
        sa.Column("numero", sa.String(128), nullable=True),
        sa.Column("emissao", sa.Date(), nullable=True),
        sa.Column("validade", sa.Date(), nullable=True, index=True),
        sa.Column("orgao_emissor", sa.String(255), nullable=True),
        sa.Column("anexo_path", sa.String(1024), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_empresa_documentos_cnpj_tipo",
        "empresa_documentos",
        ["empresa_cnpj", "tipo"],
    )

    # --- 5. diagnostico_runs --------------------------------------------
    op.create_table(
        "diagnostico_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="running",
        ),  # running | done | error
        sa.Column("scope", sa.String(32), nullable=True),  # all | dp | sst | frota | empresa | obra
        sa.Column("triggered_by", sa.String(255), nullable=True),  # email do user ou "system"
        sa.Column("total_findings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ausente_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vencido_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vencendo_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_diagnostico_runs_started_at",
        "diagnostico_runs",
        ["started_at"],
    )

    # --- 6. diagnostico_findings ----------------------------------------
    op.create_table(
        "diagnostico_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("diagnostico_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("area", sa.String(32), nullable=False, index=True),  # dp | sst | frota | empresa | obra
        sa.Column("entity_type", sa.String(64), nullable=False),  # employee | veiculo | empresa | obra
        sa.Column("entity_id", sa.Integer(), nullable=True, index=True),
        sa.Column("entity_label", sa.String(255), nullable=False),  # nome funcionario / placa / cnpj / codigo obra
        sa.Column("doc_tipo", sa.String(64), nullable=False, index=True),
        sa.Column("doc_label", sa.String(255), nullable=True),  # label legivel pra UI
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            index=True,
        ),  # ok | ausente | vencido | vencendo
        sa.Column("validade", sa.Date(), nullable=True),
        sa.Column("dias_para_vencimento", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_diagnostico_findings_run_area_status",
        "diagnostico_findings",
        ["run_id", "area", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_diagnostico_findings_run_area_status", "diagnostico_findings")
    op.drop_table("diagnostico_findings")
    op.drop_index("ix_diagnostico_runs_started_at", "diagnostico_runs")
    op.drop_table("diagnostico_runs")
    op.drop_index("ix_empresa_documentos_cnpj_tipo", "empresa_documentos")
    op.drop_table("empresa_documentos")
    op.drop_column("dp_employees", "is_eletricista")
    op.drop_column("dp_employees", "is_alturas")
    op.drop_column("dp_employees", "is_admin_office")
    op.drop_column("dp_employees", "is_operador_maquina")
    op.drop_column("dp_employees", "is_motorista")
    op.drop_index("ix_dp_employee_documents_emp_tipo", "dp_employee_documents")
    op.drop_table("dp_employee_documents")
    op.drop_table("obras_documentos")
    op.drop_table("obras_obra")
