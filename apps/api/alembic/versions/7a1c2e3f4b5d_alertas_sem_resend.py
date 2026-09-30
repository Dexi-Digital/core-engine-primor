"""alertas sem Resend: notificacao sempre + e-mail Microsoft 365

Revision ID: 7a1c2e3f4b5d
Revises: 5e3cdd212e76
Create Date: 2026-09-30

O Resend foi cortado. Os alertas de vencimento (certidoes, ASO,
afastamentos INSS, contratos) passam a criar notificacao na plataforma
sempre e, opcionalmente, e-mail pelo Microsoft 365 (Graph sendMail).

- Remove `resend_message_id` das 4 tabelas de log de alerta e do log de
  boletins. O Graph nao devolve id de mensagem, e o boletim nao manda
  e-mail desde 21/09/2026. Ids antigos do Resend sao descartados (nao
  ha mais onde consulta-los).
- Adiciona `email_status` (enviado | falhou | nao_configurado) e
  `email_error` nas 4 tabelas de log de alerta: o `status` existente
  passa a significar "alerta despachado" (notificacao criada), e o
  resultado do e-mail fica separado.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "7a1c2e3f4b5d"
down_revision = "5e3cdd212e76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABELAS_ALERTA = (
    "certidoes_alertas_log",
    "dp_aso_alertas_log",
    "dp_afastamentos_alertas_log",
    "contratos_alertas_log",
)


def upgrade() -> None:
    for tabela in _TABELAS_ALERTA:
        with op.batch_alter_table(tabela) as batch:
            batch.drop_column("resend_message_id")
            batch.add_column(sa.Column("email_status", sa.String(length=32), nullable=True))
            batch.add_column(sa.Column("email_error", sa.String(length=1024), nullable=True))
    with op.batch_alter_table("licitacoes_boletins_log") as batch:
        batch.drop_column("resend_message_id")


def downgrade() -> None:
    with op.batch_alter_table("licitacoes_boletins_log") as batch:
        batch.add_column(
            sa.Column("resend_message_id", sa.String(length=128), nullable=True)
        )
    for tabela in reversed(_TABELAS_ALERTA):
        with op.batch_alter_table(tabela) as batch:
            batch.drop_column("email_error")
            batch.drop_column("email_status")
            batch.add_column(
                sa.Column("resend_message_id", sa.String(length=128), nullable=True)
            )
