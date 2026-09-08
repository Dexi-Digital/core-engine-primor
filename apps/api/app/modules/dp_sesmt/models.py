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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _bool_false():
    """Helper para `server_default` cross-DB de boolean false."""
    return text("false")

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

    # --- flags SST (D1) ---
    # Disparam regras condicionais no checklist do diagnostico
    # documental (ex.: is_motorista exige exame toxicologico,
    # is_operador_maquina exige NR-12 + AET, is_alturas exige NR-35).
    is_motorista: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=_bool_false()
    )
    is_operador_maquina: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=_bool_false()
    )
    is_admin_office: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=_bool_false()
    )
    is_alturas: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=_bool_false()
    )
    is_eletricista: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=_bool_false()
    )

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


# Tipos de beneficio INSS (Demanda 4 - acompanhamento de afastados).
# Codigos sao os usados no Meu INSS/eSocial. "OUTRO" cobre licenca-
# maternidade, ausencia justificada que nao gere beneficio etc.
BENEFICIO_B31 = "B31"  # auxilio por incapacidade temporaria (auxilio-doenca)
BENEFICIO_B91 = "B91"  # auxilio-acidente (apos consolidacao de sequela)
BENEFICIO_B32 = "B32"  # aposentadoria por invalidez
BENEFICIO_OUTRO = "OUTRO"
BENEFICIOS_VALIDOS = frozenset(
    {BENEFICIO_B31, BENEFICIO_B91, BENEFICIO_B32, BENEFICIO_OUTRO}
)

AFASTAMENTO_EM_ANDAMENTO = "em_andamento"
AFASTAMENTO_ENCERRADO = "encerrado"
AFASTAMENTO_REABILITADO = "reabilitado"
AFASTAMENTO_STATUSES_VALIDOS = frozenset(
    {AFASTAMENTO_EM_ANDAMENTO, AFASTAMENTO_ENCERRADO, AFASTAMENTO_REABILITADO}
)


class Afastamento(Base):
    """Acompanhamento de afastamento INSS (Demanda 4).

    Cobre o ciclo do beneficio: data de inicio, DCB (Data de Cessacao do
    Beneficio = previsao do fim do auxilio), data da pericia medica
    (proxima ou ultima -- a UI sobreescreve), e status atual.

    Mantemos o numero do beneficio + CID para o RH conseguir abrir o
    Meu INSS rapidamente sem precisar voltar pro arquivo fisico. Nao
    e obrigatorio (nem todo afastamento gera beneficio do INSS, ex.:
    licenca nao remunerada de ate 15 dias).

    Alertas (ver `afastamentos.py`):
    - DCB se aproximando (30/15/7/0 dias) -- empresa precisa avisar
      o INSS se vai pedir prorrogacao ou nao.
    - Pericia medica (15/7/0 dias) -- funcionario precisa comparecer.
    """

    __tablename__ = "dp_afastamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("dp_employees.id", ondelete="CASCADE"), index=True
    )
    beneficio_tipo: Mapped[str] = mapped_column(
        String(16), default=BENEFICIO_B31, server_default=BENEFICIO_B31
    )
    numero_beneficio: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    cid: Mapped[str | None] = mapped_column(String(16), nullable=True)
    data_inicio: Mapped[date] = mapped_column(Date)
    dcb: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    data_pericia: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True
    )
    data_retorno: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        default=AFASTAMENTO_EM_ANDAMENTO,
        server_default=AFASTAMENTO_EM_ANDAMENTO,
        index=True,
    )
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AfastamentoAlertaLog(Base):
    """Idempotencia dos alertas de afastamento INSS.

    Mesma logica do `EmployeeAsoAlertaLog`, com a diferenca que aqui o
    unique e `(afastamento_id, kind, janela)` -- ha dois tipos de
    alerta (DCB e pericia) e cada tipo tem suas proprias janelas.
    """

    __tablename__ = "dp_afastamentos_alertas_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    afastamento_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("dp_afastamentos.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16))  # "dcb" | "pericia"
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
            "afastamento_id",
            "kind",
            "janela",
            name="uq_afastamento_alerta_kind_janela",
        ),
    )


# ------------------------- D1: Documentos por funcionario ------------------

# Tipos canonicos de doc por funcionario (para uso em validators e UI).
# A coluna `tipo` no banco e String livre, para permitir adicionar novos
# tipos sem migration. Esses sao os tipos esperados pelo motor de
# diagnostico documental (D1) -- ver app.modules.diagnostico.checklists.
# Documentos contratuais basicos (CLT) -- exigidos para todo funcionario
# CLT (avaliados pelo checklist DP do diagnostico documental).
DOC_EMP_CTPS = "CTPS"  # Carteira de Trabalho e Previdencia Social
DOC_EMP_CONTRATO_TRABALHO = "CONTRATO_TRABALHO"
DOC_EMP_FICHA_REGISTRO = "FICHA_REGISTRO"

DOC_EMP_NR10 = "NR10"  # Eletricidade -- so se is_eletricista
DOC_EMP_NR12 = "NR12"  # Maquinas/equipamentos -- so se is_operador_maquina
DOC_EMP_NR18 = "NR18"  # Construcao civil -- obrigatorio em obras
DOC_EMP_NR35 = "NR35"  # Trabalho em altura -- so se is_alturas
DOC_EMP_TOXICOLOGICO = "TOXICOLOGICO"  # so se is_motorista
DOC_EMP_OS = "ORDEM_SERVICO"  # OS de SST (NR-1)
DOC_EMP_LISTA_INTEGRACAO = "LISTA_INTEGRACAO"
DOC_EMP_FICHA_EPI = "FICHA_EPI"
DOC_EMP_TERMO_LGPD = "TERMO_LGPD"
DOC_EMP_CONTRATO_EXPERIENCIA = "CONTRATO_EXPERIENCIA"
DOC_EMP_ACORDO_COMPENSACAO = "ACORDO_COMPENSACAO_HORAS"
DOC_EMP_TERMO_VT = "TERMO_VT"  # renuncia ou aceite vale-transporte
DOC_EMP_DECL_FAMILIA = "DECLARACAO_ENCARGOS_FAMILIA"
DOC_EMP_FICHA_SALARIO_FAMILIA = "FICHA_SALARIO_FAMILIA"
DOC_EMP_TERMO_RESPONSABILIDADE = "TERMO_RESPONSABILIDADE_CRACHA_EMAIL"
DOC_EMP_RCT = "RCT"  # rescisao do contrato (demissao)
DOC_EMP_PPP = "PPP"  # perfil profissiografico previdenciario
DOC_EMP_OUTRO = "OUTRO"

DOC_EMP_TIPOS_VALIDOS: frozenset[str] = frozenset(
    {
        DOC_EMP_CTPS,
        DOC_EMP_CONTRATO_TRABALHO,
        DOC_EMP_FICHA_REGISTRO,
        DOC_EMP_NR10,
        DOC_EMP_NR12,
        DOC_EMP_NR18,
        DOC_EMP_NR35,
        DOC_EMP_TOXICOLOGICO,
        DOC_EMP_OS,
        DOC_EMP_LISTA_INTEGRACAO,
        DOC_EMP_FICHA_EPI,
        DOC_EMP_TERMO_LGPD,
        DOC_EMP_CONTRATO_EXPERIENCIA,
        DOC_EMP_ACORDO_COMPENSACAO,
        DOC_EMP_TERMO_VT,
        DOC_EMP_DECL_FAMILIA,
        DOC_EMP_FICHA_SALARIO_FAMILIA,
        DOC_EMP_TERMO_RESPONSABILIDADE,
        DOC_EMP_RCT,
        DOC_EMP_PPP,
        DOC_EMP_OUTRO,
    }
)

DOC_EMP_LABELS: dict[str, str] = {
    DOC_EMP_CTPS: "CTPS (Carteira de Trabalho)",
    DOC_EMP_CONTRATO_TRABALHO: "Contrato de trabalho",
    DOC_EMP_FICHA_REGISTRO: "Ficha de registro",
    DOC_EMP_NR10: "NR-10 (Eletricidade)",
    DOC_EMP_NR12: "NR-12 (Maquinas/Equipamentos)",
    DOC_EMP_NR18: "NR-18 (Construcao Civil)",
    DOC_EMP_NR35: "NR-35 (Trabalho em Altura)",
    DOC_EMP_TOXICOLOGICO: "Exame toxicologico",
    DOC_EMP_OS: "Ordem de Servico (SST)",
    DOC_EMP_LISTA_INTEGRACAO: "Lista de Integracao",
    DOC_EMP_FICHA_EPI: "Ficha de EPI",
    DOC_EMP_TERMO_LGPD: "Termo de Consentimento LGPD",
    DOC_EMP_CONTRATO_EXPERIENCIA: "Contrato de Experiencia",
    DOC_EMP_ACORDO_COMPENSACAO: "Acordo de Compensacao de Horas",
    DOC_EMP_TERMO_VT: "Termo de Vale-Transporte",
    DOC_EMP_DECL_FAMILIA: "Declaracao de Encargos de Familia",
    DOC_EMP_FICHA_SALARIO_FAMILIA: "Ficha de Salario-Familia",
    DOC_EMP_TERMO_RESPONSABILIDADE: "Termo de Responsabilidade (cracha/email)",
    DOC_EMP_RCT: "Rescisao do Contrato (RCT)",
    DOC_EMP_PPP: "Perfil Profissiografico Previdenciario (PPP)",
    DOC_EMP_OUTRO: "Outro",
}


class EmployeeDocument(Base):
    """Documento generico vinculado a um funcionario.

    Cobre os doc types listados em `DOC_EMP_TIPOS_VALIDOS`. ASO continua
    nas colunas `aso_*` em `Employee` (compat com A.2 alertas em prod).
    Multiplas rows do mesmo tipo sao permitidas (historico anual de
    NR-18, etc.); a UI / motor de diagnostico mostra a mais recente.
    """

    __tablename__ = "dp_employee_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("dp_employees.id", ondelete="CASCADE"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(64), index=True)
    numero: Mapped[str | None] = mapped_column(String(128), nullable=True)
    emissao: Mapped[date | None] = mapped_column(Date, nullable=True)
    validade: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True
    )
    anexo_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(32), default="manual", server_default="manual"
    )

    # --- Sync OneDrive (D1 fase 2) ---
    # Item id no Microsoft Graph, quando a row foi criada/atualizada via
    # sync da pasta OneDrive. Unique parcial `IS NOT NULL` no banco
    # (ver migration dd3e4f5a6b7c) garante idempotencia.
    onedrive_item_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    onedrive_path: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    onedrive_last_modified: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onedrive_sync_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("onedrive_sync_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- Sync OnSafety (pull de fichas de EPI, Squad 2) ---
    # Id do `controle_epi` na OnSafety quando a row veio do pull --
    # chave de idempotencia do sync (re-pull atualiza em vez de
    # duplicar). Unique parcial `IS NOT NULL` no banco (migration
    # b8c9d0e1f2a3), mesmo padrao de `onedrive_item_id`.
    onsafety_external_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Obra (= `establishment`/Projeto na OnSafety) em que o documento
    # foi emitido. Resolvido no pull por `Projeto.codigoExterno` ->
    # `obras_obra.codigo`, com fallback pelo codigo no nome (mesmo
    # padrao dos locais de trabalho do Tangerino, ADR-003). Fica NULL
    # quando o documento nao tem obra ou quando ela nao esta cadastrada
    # -- nunca bloqueia a ingestao do documento.
    obra_id: Mapped[int | None] = mapped_column(
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


class OnboardingSyncRun(Base):
    """Historico append-only do push de onboarding para sistemas externos.

    Etapa 3 da integracao OnSafety (ADR-001): cada tentativa de
    `create_or_update` vira uma row -- sucesso ou erro -- para a UI
    mostrar o status por sistema e para retry sem perder rastro
    (mesma semantica de `frota_consultas_detran`). `sistema` preve
    Dominio/Onvio/Tangerino no futuro sem nova tabela.

    `correlation_id` e deterministico por (employee, sistema) --
    reprocessos usam o mesmo id e a OnSafety faz upsert por
    `codigoExterno`, entao rodar N vezes nao duplica trabalhador la.
    """

    __tablename__ = "dp_onboarding_syncs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("dp_employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sistema: Mapped[str] = mapped_column(String(32), index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))  # ok | erro
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_msg: Mapped[str | None] = mapped_column(String(500), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
