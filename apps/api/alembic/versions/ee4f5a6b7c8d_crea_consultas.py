"""crea_consultas: log de consultas CREA via Infosimples (D.6 fase 2)

Revision ID: ee4f5a6b7c8d
Revises: dd3e4f5a6b7c
Create Date: 2026-04-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "ee4f5a6b7c8d"
down_revision = "dd3e4f5a6b7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # D.6 fase 2 -- log de consultas CREA via Infosimples (PR #28).
    # Mesma estrategia da `frota_consultas_detran` (PR #14):
    #   - cada chamada gera uma row independente
    #   - `payload` guarda a resposta NORMALIZADA (UI consome direto)
    #   - `status` = 'pendente' | 'ok' | 'erro' | 'mock'
    #   - `source` = 'infosimples' | 'infosimples_mock'
    # Diferente do Detran: nao temos FK pra um veiculo/funcionario
    # especifico -- consulta CREA pode ser ad-hoc (validar uma ART
    # nova antes de cadastrar). Quando o operador clica "Importar
    # como certidao", criamos uma `CertidaoEmpresa` separada e
    # gravamos `certidao_id` aqui pra rastreabilidade.
    op.create_table(
        "crea_consultas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uf", sa.String(length=2), nullable=False, index=True),
        # 'art' | 'profissional' | 'empresa'
        sa.Column("tipo", sa.String(length=16), nullable=False, index=True),
        # numero ART, registro CREA do profissional, ou CNPJ
        sa.Column(
            "identificador", sa.String(length=64), nullable=False, index=True
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pendente",
            index=True,
        ),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="infosimples_mock",
        ),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        # FK opcional: quando a consulta vira uma `CertidaoEmpresa`
        # (via `POST /importar-art`), gravamos aqui o id pra UI poder
        # mostrar "ja importada" no historico.
        sa.Column(
            "certidao_id",
            sa.Integer(),
            sa.ForeignKey("certidoes_empresa.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # UI mais comum: "ultima consulta de uma ART/CNPJ/registro". Composite
    # com `executed_at` evita filesort sob lista paginada por
    # tipo+identificador. Sem DESC explicito (SQLite + Postgres scan
    # bidirecional do mesmo b-tree).
    op.create_index(
        "ix_crea_consultas_tipo_ident_executed",
        "crea_consultas",
        ["tipo", "identificador", "executed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crea_consultas_tipo_ident_executed",
        table_name="crea_consultas",
    )
    op.drop_table("crea_consultas")
