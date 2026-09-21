"""Contencioso juridico -- espelho do que o EasyJur da Primor contem.

Medido em 19/09/2026: 453 processos (TRT03 296, TJMG 109) e ~12 mil
andamentos. E contencioso TRABALHISTA, nao contratos -- o modulo de
contratos do EasyJur tem um unico registro, um template em branco.

Espelho, nao fonte: o EasyJur continua sendo onde o escritorio
trabalha. Este pull e somente leitura e nunca escreve de volta.

Segue o formato do `app.modules.ponto`: chave natural do fornecedor,
vinculo com obra nullable, e log de sync com `source` na chave.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SOURCES_SYNC: tuple[str, ...] = ("beat", "manual")


class Processo(Base):
    __tablename__ = "juridico_processos"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Id numerico do EasyJur -- estavel, e a chave do upsert.
    easyjur_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    numero_cnj: Mapped[str | None] = mapped_column(String(32), index=True)

    # --- alta cobertura (CNJ 100%, tribunal 93%, comarca 86%, area 66%)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    area: Mapped[str | None] = mapped_column(String(64), index=True)
    tribunal: Mapped[str | None] = mapped_column(String(32), index=True)
    instancia: Mapped[str | None] = mapped_column(String(32))
    comarca: Mapped[str | None] = mapped_column(String(128))
    titulo: Mapped[str | None] = mapped_column(String(255))
    cliente: Mapped[str | None] = mapped_column(String(255))
    contrario: Mapped[str | None] = mapped_column(String(255))

    # --- ESPARSOS (tipo_acao 15%, risco 11%, resultado 8%, fase 6%).
    # None = o escritorio nao preencheu. Nunca agregar sem devolver o
    # denominador junto -- ver `service.resumo`.
    tipo_acao: Mapped[str | None] = mapped_column(String(255))
    risco: Mapped[str | None] = mapped_column(String(64))
    fase_atual: Mapped[str | None] = mapped_column(String(128))
    resultado: Mapped[str | None] = mapped_column(String(128))

    grupos: Mapped[str | None] = mapped_column(Text)

    # Codigo extraido da tag "OBRAS <Local> - z231". Nao inventamos
    # obra: codigo sem obra cadastrada fica com `obra_id` nulo e aparece
    # como pendencia. So 32 dos 453 processos tem a tag.
    codigo_obra: Mapped[str | None] = mapped_column(String(32), index=True)
    obra_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("obras_obra.id", ondelete="SET NULL"), index=True
    )

    sincronizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Andamento(Base):
    __tablename__ = "juridico_andamentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    easyjur_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)

    # O CNJ fica gravado MESMO com `processo_id`: o feed cita processos
    # por numero, e nem todo numero citado esta entre os listados.
    numero_cnj: Mapped[str] = mapped_column(String(32), index=True)
    processo_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("juridico_processos.id", ondelete="SET NULL"),
        index=True,
    )

    tipo: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(32), index=True)
    # Pode trazer "?" no lugar de acento -- defeito do export deles,
    # preservado como veio em vez de adivinhado.
    descricao: Mapped[str | None] = mapped_column(Text)
    data: Mapped[date | None] = mapped_column(Date, index=True)

    __table_args__ = (Index("ix_juridico_andamentos_feed", "data", "id"),)


class SyncLog(Base):
    __tablename__ = "juridico_sync_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    janela: Mapped[date] = mapped_column(Date)

    processos: Mapped[int] = mapped_column(Integer, default=0)
    andamentos: Mapped[int] = mapped_column(Integer, default=0)
    # O que o EasyJur DISSE ter, contra o que coletamos.
    total_declarado: Mapped[int | None] = mapped_column(Integer)
    divergencia: Mapped[bool] = mapped_column(Boolean, default=False)
    erro: Mapped[str | None] = mapped_column(String(1024))

    executado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # `source` na chave: um "sincronizar agora" manual nao pode
        # queimar a janela do job agendado.
        UniqueConstraint("source", "janela", name="uq_juridico_sync_source_janela"),
    )
