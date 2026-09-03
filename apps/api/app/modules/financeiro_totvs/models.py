"""Tabelas de destino do pull do TOTVS RM (Modulo C).

DUAS tabelas, de proposito:

`totvs_lancamentos` -- o dado. Unique em `external_id` SOZINHO, igual
ao PNCP (`licitacoes.external_id`): o mesmo lancamento do RM e uma
linha so, tenha ele chegado por REST ou por wsConsultaSQL. `extractor`
e proveniencia, nao chave -- se entrasse na chave, trocar de caminho de
extracao duplicaria a base inteira.

`totvs_sync_log` -- a execucao. Unique em (`source`, `janela`), com
`source` na chave DESDE O PRIMEIRO COMMIT (decisao #5). E aqui que mora
o bug do `dispatch_contrato_alerts_endpoint`: em `contratos_alertas_log`
a unique key nao distingue origem, entao um disparo manual marca a
janela como feita e SUPRIME o beat do dia. Quando entrar o botao
"sincronizar agora" no dash, `source="manual"` e `source="beat"` ocupam
linhas distintas e nenhum come a janela do outro.

Decisao #7: nada de coluna `totvs_*` no `Contrato`. Mesmo precedente da
assinatura digital registrado em `financeiro_contratos/models.py`
(04/08). A reconciliacao com contratos e por documento
(`contraparte_documento` <-> `FCFO.CGCCFO`), e depende da normalizacao
so-digitos daquela coluna -- ticket separado (decisao #8).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Origens de disparo do pull. `beat` e o agendado; `manual` e o botao
# do dash (ainda nao existe, mas a chave ja separa os dois).
SOURCES_SYNC: tuple[str, ...] = ("beat", "manual")


class TotvsLancamento(Base):
    """Um lancamento financeiro lido do RM (tabela FLAN).

    `external_id` e a chave natural do RM no formato
    `codcoligada-codfilial-idlan` -- A CONFIRMAR com o time RM Gestao
    Financeira (a Central de Atendimento empurrou essa pergunta para
    eles em 24/08/2026). Se `IDLAN` sozinho ja for globalmente unico, a
    chave encolhe sem quebrar nada: o prefixo continua estavel.

    `valor` em `Numeric(20, 2)` -- mesma convencao monetaria do resto do
    repo. Nunca `float`: o RM devolve string e a conversao passa por
    `app.core.valores.coerce_valor_rm`.
    """

    __tablename__ = "totvs_lancamentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    codcoligada: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    codfilial: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idlan: Mapped[int | None] = mapped_column(Integer, nullable=True)

    valor: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    # So-digitos: chave de match com o FCFO do RM e com
    # `Contrato.contraparte_documento` (que ainda precisa da mesma
    # normalizacao -- decisao #8).
    contraparte_documento: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    contraparte_nome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_vencimento: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    data_emissao: Mapped[date | None] = mapped_column(Date, nullable=True)
    status_rm: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Proveniencia, NAO chave: `totvs_rest` | `totvs_consultasql` | `totvs_mock`.
    extractor: Mapped[str] = mapped_column(String(32), index=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_totvs_lancamentos_doc_venc", "contraparte_documento", "data_vencimento"),
    )


class TotvsSyncLog(Base):
    """Uma execucao do pull, por (origem, janela).

    `source` na unique key desde o primeiro commit -- ver docstring do
    modulo e a do `dispatch_contrato_alerts_endpoint` em
    `financeiro_contratos/router.py`.
    """

    __tablename__ = "totvs_sync_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    janela: Mapped[str] = mapped_column(String(32), index=True)
    extractor: Mapped[str] = mapped_column(String(32))

    status: Mapped[str] = mapped_column(String(16), default="ok")
    lidos: Mapped[int] = mapped_column(Integer, default=0)
    gravados: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("source", "janela", name="uq_totvs_sync_source_janela"),
    )
