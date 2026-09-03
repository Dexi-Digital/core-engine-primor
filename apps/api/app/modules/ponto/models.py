"""Ponto eletronico (Solides/Tangerino) -- Modulos A e B.

Origem do desenho: exploracao da API REAL da conta da Primor em
27/08/2026 (395 funcionarios, 83 locais de trabalho). O ADR-003 tratava
o vinculo funcionario -> obra como pergunta em aberto ("workplace vs
geolocalizacao"); a API respondeu -- geolocalizacao nao existe em
nenhum modelo, e os locais de trabalho SAO as obras.

Tres tabelas de dado + um log de execucao:

`ponto_locais_trabalho` -- os workplaces. Guardam `codigo_obra`
(extraido do nome) e `obra_id` nullable. Nao inventamos obra: local que
aponta para uma obra ainda nao cadastrada fica com codigo e sem
`obra_id`, e a tela mostra como pendencia.

`ponto_funcionarios` -- o funcionario COMO O SOLIDES o conhece, ligado
por CPF ao `dp_employees` quando existir. O DP continua sendo a fonte
da verdade do cadastro: este pull NUNCA cria funcionario la.

`ponto_batidas` -- as batidas, com os epochs em milissegundos ja
convertidos para datetime.

`ponto_sync_log` -- unique em (`source`, `janela`), com `source` na
chave desde o primeiro commit. Mesmo precedente do TOTVS e do bug do
`dispatch_contrato_alerts_endpoint`: sem isso, um "sincronizar agora"
manual queima a janela do job agendado.
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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SOURCES_SYNC: tuple[str, ...] = ("beat", "manual")


class PontoLocalTrabalho(Base):
    """Local de trabalho (workplace) do Solides.

    `tangerino_id` e a chave natural -- id numerico deles, estavel.
    `nome_normalizado` existe para detectar duplicidade de cadastro: na
    conta real, "Obra 010 CTC" aparece duas vezes com ids diferentes.
    """

    __tablename__ = "ponto_locais_trabalho"

    id: Mapped[int] = mapped_column(primary_key=True)
    tangerino_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)

    nome: Mapped[str] = mapped_column(String(255))
    nome_normalizado: Mapped[str] = mapped_column(String(255), index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)

    # Codigo extraido do nome ("Obra 243" -> "243"). None em local
    # administrativo -- e o comportamento correto, nao falha de match.
    codigo_obra: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    obra_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("obras_obra.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PontoFuncionario(Base):
    """Funcionario como o Solides o conhece.

    `employee_id` liga no `dp_employees` por CPF. Fica NULL quando o CPF
    nao existe no DP -- e isso e proposital: o cadastro do DP e a fonte
    da verdade e este pull nao cria funcionario la. A tela lista os sem
    vinculo como pendencia de cadastro.
    """

    __tablename__ = "ponto_funcionarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    tangerino_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)

    nome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cpf: Mapped[str | None] = mapped_column(String(11), nullable=True, index=True)
    pis: Mapped[str | None] = mapped_column(String(11), nullable=True)
    data_admissao: Mapped[date | None] = mapped_column(Date, nullable=True)
    demitido: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    employee_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("dp_employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Guardado como id EXTERNO (do Solides), nao FK: o pull de
    # funcionarios pode rodar antes do de locais.
    local_trabalho_externo_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    local_trabalho_nome: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PontoBatida(Base):
    """Uma batida (jornada) de um funcionario num dia.

    `external_id` = `{tangerino_id}-{data}-{inicio_epoch}`: o Solides
    nao expoe id proprio da batida, entao a chave natural e essa
    combinacao. Mesmo padrao de upsert do PNCP e do TOTVS.

    Os `*_ts` da API vem em epoch de MILISSEGUNDOS (confirmado contra a
    API real em 27/08/2026) e sao convertidos aqui na entrada.
    """

    __tablename__ = "ponto_batidas"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    tangerino_employee_id: Mapped[int] = mapped_column(Integer, index=True)
    funcionario_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ponto_funcionarios.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    data_trabalho: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True
    )
    inicio: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fim: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    segundos_trabalhados: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_ponto_batidas_func_data", "funcionario_id", "data_trabalho"),
    )


class PontoSyncLog(Base):
    """Uma execucao do pull, por (origem, janela).

    `source` na unique key desde o primeiro commit -- ver docstring do
    modulo.
    """

    __tablename__ = "ponto_sync_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    janela: Mapped[str] = mapped_column(String(48), index=True)
    recurso: Mapped[str] = mapped_column(String(32))

    status: Mapped[str] = mapped_column(String(16), default="ok")
    lidos: Mapped[int] = mapped_column(Integer, default=0)
    gravados: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "source", "janela", "recurso", name="uq_ponto_sync_source_janela_recurso"
        ),
    )
