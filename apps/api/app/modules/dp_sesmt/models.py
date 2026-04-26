"""ORM models for DP / SESMT (Modulo A).

Inicialmente este modulo nao depende de integracao com Dominio/Onvio:
o cadastro do funcionario e feito manualmente na UI e enriquecido sob
demanda via APIs publicas (ViaCEP, BrasilAPI) e DirectData (paga).
Quando as credenciais Onvio/Dominio HR estiverem disponiveis, o sync
sera adicionado em um modulo separado (`onboarding_sync`) que fara
upsert nesta mesma tabela.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# Fontes de cadastro -- util para auditar de onde veio o funcionario.
SOURCE_MANUAL = "manual"
SOURCE_DIRECTDATA = "directdata"
SOURCE_DOMINIO = "dominio"  # reservado para o futuro sync

# Status canonicos.
STATUS_ATIVO = "ativo"
STATUS_AFASTADO = "afastado"  # INSS, ferias prolongadas, licenca
STATUS_DESLIGADO = "desligado"
STATUSES_VALIDOS = frozenset({STATUS_ATIVO, STATUS_AFASTADO, STATUS_DESLIGADO})


class Employee(Base):
    """Funcionario / colaborador da empresa.

    O CPF e a chave natural; matricula e secundaria (varios sistemas
    legados podem nao ter sincronizado). Campos opcionais para suportar
    cadastro incremental -- na admissao tipica voce nao tem RG/PIS no
    primeiro momento.
    """

    __tablename__ = "dp_employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- chave natural + identificacao ---
    cpf: Mapped[str] = mapped_column(String(14), unique=True, index=True)
    matricula: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    nome_completo: Mapped[str] = mapped_column(String(255))
    nome_social: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- documentos ---
    rg: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rg_orgao_emissor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pis_pasep: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ctps_numero: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ctps_serie: Mapped[str | None] = mapped_column(String(16), nullable=True)
    titulo_eleitor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cnh_numero: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cnh_categoria: Mapped[str | None] = mapped_column(String(8), nullable=True)
    cnh_validade: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- pessoal ---
    data_nascimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    sexo: Mapped[str | None] = mapped_column(String(16), nullable=True)
    estado_civil: Mapped[str | None] = mapped_column(String(32), nullable=True)
    escolaridade: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nome_mae: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nome_pai: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nacionalidade: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default="Brasileira"
    )

    # --- contato ---
    telefone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- endereco ---
    cep: Mapped[str | None] = mapped_column(String(16), nullable=True)
    logradouro: Mapped[str | None] = mapped_column(String(255), nullable=True)
    numero: Mapped[str | None] = mapped_column(String(16), nullable=True)
    complemento: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bairro: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cidade: Mapped[str | None] = mapped_column(String(128), nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # --- contrato / vinculo ---
    cargo: Mapped[str] = mapped_column(String(100))
    obra: Mapped[str | None] = mapped_column(String(128), nullable=True)
    setor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tipo_contrato: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # CLT, PJ, Estagio, Aprendiz
    salario_base: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    data_admissao: Mapped[date | None] = mapped_column(Date, nullable=True)
    data_desligamento: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_ATIVO, server_default=STATUS_ATIVO, index=True
    )

    # --- ASO (atestado de saude ocupacional) ---
    aso_data: Mapped[date | None] = mapped_column(Date, nullable=True)
    aso_validade: Mapped[date | None] = mapped_column(Date, nullable=True)
    aso_resultado: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # apto, inapto, apto_restricoes

    # --- metadata ---
    source: Mapped[str] = mapped_column(
        String(32), default=SOURCE_MANUAL, server_default=SOURCE_MANUAL
    )
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # --- relations ---
    empregos_anteriores: Mapped[list[EmpregoAnterior]] = relationship(
        back_populates="employee",
        cascade="all, delete-orphan",
    )


class EmpregoAnterior(Base):
    """Histórico de empregos anteriores -- alimenta o dossie de admissao."""

    __tablename__ = "dp_empregos_anteriores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("dp_employees.id", ondelete="CASCADE"), index=True
    )
    empresa_cnpj: Mapped[str | None] = mapped_column(String(20), nullable=True)
    empresa_razao_social: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    cargo: Mapped[str | None] = mapped_column(String(128), nullable=True)
    inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    fim: Mapped[date | None] = mapped_column(Date, nullable=True)
    motivo_desligamento: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)

    employee: Mapped[Employee] = relationship(back_populates="empregos_anteriores")


class DossieConsultaLog(Base):
    """Auditoria de consultas a APIs externas para o dossie.

    Mantemos isso para LGPD: toda consulta a CPF/CNPJ/CEP de um
    funcionario fica registrada (quem consultou, quando, qual fonte,
    sucesso/erro). Tambem ajuda a debitar custo de APIs pagas
    (DirectData) por funcionario.
    """

    __tablename__ = "dp_dossie_consultas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("dp_employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    fonte: Mapped[str] = mapped_column(
        String(32), index=True
    )  # viacep | brasilapi_cnpj | directdata_cpf
    chave_consulta: Mapped[str] = mapped_column(
        String(32)
    )  # CEP/CNPJ/CPF consultado
    sucesso: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EmployeeAsoAlertaLog(Base):
    """Idempotencia do cron diario de alertas de ASO (A.2).

    Mesmo padrao do `CertidaoAlertaLog`: UniqueConstraint
    `(employee_id, janela)` impede que o cron diario reenvie email da
    mesma janela do mesmo funcionario. As janelas (30/15/7/0) sao
    armazenadas como string ("30d", "15d"...) para manter compat com
    a serializacao usada no service de certidoes.

    Status:
    - `sent`: email entregue ao Resend (resend_message_id presente)
    - `failed`: erro ao enviar (error_message presente). Cron tenta
      novamente na proxima execucao -- a row e atualizada in-place.
    """

    __tablename__ = "dp_aso_alertas_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("dp_employees.id", ondelete="CASCADE"),
        index=True,
    )
    janela: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    recipients: Mapped[list[str]] = mapped_column(JSON)
    resend_message_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="sent")
    error_message: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "janela", name="uq_aso_alerta_employee_janela"
        ),
    )
