"""frota_veiculos: cadastro de frota + documentos por veiculo (B.1)

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-04-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Modulo B.1 -- cadastro de veiculos da frota. Pre-requisito para
    # B.2 (parte diaria por veiculo) e B.3 (RPA Detran -- multas/IPVA
    # consultados pela placa). Mantemos `placa` como chave natural mas
    # sem unique constraint hard porque trocas de placa (Mercosul x
    # antiga, troca por sinistro) acontecem no mundo real e o histórico
    # precisa virar uma linha de audit_log + nova row, nao constraint
    # error.
    op.create_table(
        "frota_veiculos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("placa", sa.String(8), nullable=False, index=True),
        sa.Column("renavam", sa.String(11), nullable=True, index=True),
        sa.Column("chassi", sa.String(17), nullable=True, index=True),
        sa.Column("marca", sa.String(64), nullable=True),
        sa.Column("modelo", sa.String(128), nullable=True),
        sa.Column("ano_fabricacao", sa.Integer(), nullable=True),
        sa.Column("ano_modelo", sa.Integer(), nullable=True),
        sa.Column("cor", sa.String(32), nullable=True),
        # Tipo nao restringe -- frota mistura caminhao, carro pequeno,
        # van, maquina pesada (escavadeira, retroescavadeira). Operador
        # digita texto livre ou escolhe de lista controlada na UI.
        sa.Column("tipo", sa.String(32), nullable=True, index=True),
        sa.Column("combustivel", sa.String(16), nullable=True),
        sa.Column("obra", sa.String(128), nullable=True, index=True),
        sa.Column("setor", sa.String(64), nullable=True),
        sa.Column("km_atual", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="ativo",
            index=True,
        ),
        sa.Column("data_aquisicao", sa.Date(), nullable=True),
        sa.Column("data_baixa", sa.Date(), nullable=True),
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
    # Indice composto (status, obra) acelera o filtro mais comum da UI:
    # "ativos da obra X". Sem ele a tabela vira full-scan a cada request.
    op.create_index(
        "ix_frota_veiculos_status_obra",
        "frota_veiculos",
        ["status", "obra"],
    )

    # Tabela de documentos do veiculo (CRLV, seguro, IPVA, licenciamento).
    # Cada documento tem validade -- a UI alerta quando ta proximo do
    # vencimento (mesmo padrao das certidoes do D.6, mas escopo veiculo
    # em vez de empresa). RPA Detran (B.3) vai popular automaticamente
    # IPVA/licenciamento; CRLV/seguro continuam manuais.
    op.create_table(
        "frota_documentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "veiculo_id",
            sa.Integer(),
            sa.ForeignKey("frota_veiculos.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tipo", sa.String(32), nullable=False, index=True),
        sa.Column("numero", sa.String(64), nullable=True),
        sa.Column("emissao", sa.Date(), nullable=True),
        sa.Column("validade", sa.Date(), nullable=True, index=True),
        sa.Column("valor", sa.Numeric(14, 2), nullable=True),
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


def downgrade() -> None:
    op.drop_table("frota_documentos")
    op.drop_index("ix_frota_veiculos_status_obra", "frota_veiculos")
    op.drop_table("frota_veiculos")
