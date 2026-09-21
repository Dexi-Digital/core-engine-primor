"""Notificacoes dentro da plataforma.

**Por que existe.** Todo aviso do sistema saia por email (Resend):
boletins de licitacao 3x/dia, alertas de certidao, ASO e afastamento.
O cliente pediu que o boletim parasse de mandar email e aparecesse na
plataforma -- e nao havia peca nenhuma de notificacao no projeto.

Nasce GENERICA de proposito. Poderia viver dentro de `licitacoes`,
mas certidoes, ASO e afastamentos tem exatamente a mesma necessidade:
fazer isso tres vezes seria escrever o mesmo sino tres vezes e depois
ter tres caixas de entrada diferentes para o mesmo usuario.

`destinatario` e o EMAIL, nao o `user_id`. Motivo: os despachos atuais
ja trabalham com lista de emails (`recipients`), e as saved queries de
boletim guardam email. Chave estrangeira para `auth_users` obrigaria
todo destinatario a ter conta antes de receber, o que hoje nao e
verdade.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Categorias conhecidas. String livre no banco (a tela so usa para o
# rotulo e o icone) -- restringir com enum obrigaria migration a cada
# modulo novo que quiser notificar.
CATEGORIA_LICITACOES = "licitacoes"
CATEGORIA_CERTIDOES = "certidoes"
CATEGORIA_DP = "dp"
CATEGORIA_FROTA = "frota"


class Notificacao(Base):
    __tablename__ = "notificacoes"

    id: Mapped[int] = mapped_column(primary_key=True)

    destinatario: Mapped[str] = mapped_column(String(255), index=True)
    categoria: Mapped[str] = mapped_column(String(32), index=True)
    titulo: Mapped[str] = mapped_column(String(255))
    corpo: Mapped[str] = mapped_column(Text)

    # Para onde o clique leva (ex.: "/licitacoes?saved_query=7").
    link: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # `None` = nao lida. Guardamos o INSTANTE, nao um booleano: saber
    # quando foi lida responde "o aviso chegou a tempo?", que um
    # booleano nao responde.
    lida_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Idempotencia do despacho. O beat roda o boletim 3x/dia sobre a
    # mesma saved query; sem isto o sino acumularia copias do mesmo
    # aviso -- e notificacao repetida ensina a ignorar notificacao.
    # Opcional: aviso avulso pode repetir de proposito.
    chave_idempotencia: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # A unicidade inclui o destinatario: o mesmo boletim vai para
        # varias pessoas, e cada uma precisa da sua copia.
        UniqueConstraint(
            "destinatario",
            "chave_idempotencia",
            name="uq_notificacoes_destinatario_chave",
        ),
        # Consulta quente: as nao lidas de uma pessoa, mais recentes
        # primeiro.
        Index("ix_notificacoes_caixa", "destinatario", "lida_em", "created_at"),
    )
