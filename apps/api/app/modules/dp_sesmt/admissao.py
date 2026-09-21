"""Jornada de admissao -- o Motor Central conduzindo, nao registrando.

**O problema.** O sistema era passivo: CRUD de funcionario, adapters que
trazem dado e crons que avisam vencimento. Admitir alguem exigia que a
pessoa soubesse, de cabeca, o que faltava e para quem mandar.

**O que muda aqui.** A jornada sabe em que etapa cada admissao esta, o
que falta para avancar, e produz o KIT -- o artefato que destrava a
contabilidade (ADR-003 D4: sem API e sem layout de importacao, a
digitacao final no Dominio fica com eles; o Motor Central automatiza
tudo ao redor).

**Etapa nao e campo livre.** `avancar()` recusa quando os requisitos
nao estao satisfeitos, e diz exatamente o que falta. Um status que
qualquer um escreve nao conduz nada -- vira outro campo para alguem
manter na mao.

**Lote por obra** (ADR-003 D4, descoberto pela sazonalidade): os picos
sao na abertura de frente de obra, uma leva de admissoes na mesma
semana. Kit um-a-um nao resolve o pico, que e a dor real -- por isso
`gerar_kits_da_obra` existe desde a primeira versao.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.modules.dp_sesmt.models import (
    ADM_CANCELADA,
    ADM_CONCLUIDA,
    ADM_DADOS_OK,
    ADM_DOCS_OK,
    ADM_KIT_ENTREGUE,
    ADM_KIT_GERADO,
    ADM_RASCUNHO,
    ADMISSAO_ETAPAS,
    DOC_EMP_CTPS,
    DOC_EMP_FICHA_REGISTRO,
    AdmissaoJornada,
    Employee,
    EmployeeDocument,
)

_AUDIT_RESOURCE = "dp_sesmt.admissao"

# Campos que o Dominio pede na admissao. Sao ESTES que o kit leva, na
# ordem -- e a mesma lista que define se a etapa `dados_ok` pode ser
# alcancada. Uma lista so, para nao divergirem.
CAMPOS_OBRIGATORIOS: tuple[tuple[str, str], ...] = (
    ("nome_completo", "Nome completo"),
    ("cpf", "CPF"),
    ("cargo", "Cargo"),
    ("data_admissao", "Data de admissao"),
    ("data_nascimento", "Data de nascimento"),
    ("rg", "RG"),
    ("pis_pasep", "PIS/PASEP"),
    ("ctps_numero", "CTPS - numero"),
    ("ctps_serie", "CTPS - serie"),
    ("nome_mae", "Nome da mae"),
)

# Documentos que precisam existir no dossie antes de o kit sair.
# Enxuto de proposito: exigir documento que a admissao nao depende so
# trava a jornada e ensina a pessoa a ignorar o sistema.
DOCUMENTOS_OBRIGATORIOS: tuple[str, ...] = (
    DOC_EMP_CTPS,
    DOC_EMP_FICHA_REGISTRO,
)


class AdmissaoError(RuntimeError):
    """Operacao invalida na jornada."""


class RequisitosNaoAtendidosError(AdmissaoError):
    """Avanco recusado: a etapa seguinte tem requisitos em aberto.

    Carrega as pendencias para a UI mostrar O QUE falta, em vez de um
    "nao foi possivel" que obriga a pessoa a adivinhar.
    """

    def __init__(self, etapa_alvo: str, pendencias: list[str]):
        super().__init__(
            f"Nao e possivel avancar para '{etapa_alvo}': "
            + "; ".join(pendencias)
        )
        self.etapa_alvo = etapa_alvo
        self.pendencias = pendencias


@dataclass
class Pendencias:
    """O que falta, separado por natureza -- cada uma tem dono diferente."""

    campos: list[str] = field(default_factory=list)
    documentos: list[str] = field(default_factory=list)

    @property
    def vazio(self) -> bool:
        return not self.campos and not self.documentos

    def como_lista(self) -> list[str]:
        return [f"campo ausente: {c}" for c in self.campos] + [
            f"documento ausente: {d}" for d in self.documentos
        ]


async def _documentos_do_funcionario(
    db: AsyncSession, employee_id: int
) -> set[str]:
    res = await db.execute(
        select(EmployeeDocument.tipo).where(
            EmployeeDocument.employee_id == employee_id
        )
    )
    return {t for t in res.scalars().all() if t}


async def calcular_pendencias(
    db: AsyncSession, employee: Employee
) -> Pendencias:
    """O que falta para esta pessoa poder ser admitida.

    Calculado sempre a partir do estado atual -- nunca armazenado. Uma
    pendencia gravada em coluna envelhece: a pessoa anexa o documento e
    o sistema continua cobrando.
    """
    faltando_campos = [
        rotulo
        for campo, rotulo in CAMPOS_OBRIGATORIOS
        if not getattr(employee, campo, None)
    ]
    tipos = await _documentos_do_funcionario(db, employee.id)
    faltando_docs = [d for d in DOCUMENTOS_OBRIGATORIOS if d not in tipos]
    return Pendencias(campos=faltando_campos, documentos=faltando_docs)


def _proxima_etapa(atual: str) -> str | None:
    if atual not in ADMISSAO_ETAPAS:
        return None
    i = ADMISSAO_ETAPAS.index(atual)
    return ADMISSAO_ETAPAS[i + 1] if i + 1 < len(ADMISSAO_ETAPAS) else None


async def iniciar(
    db: AsyncSession,
    employee: Employee,
    *,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> AdmissaoJornada:
    """Abre a jornada. Idempotente: devolve a existente se ja houver."""
    existente = (
        await db.execute(
            select(AdmissaoJornada).where(
                AdmissaoJornada.employee_id == employee.id
            )
        )
    ).scalar_one_or_none()
    if existente is not None:
        return existente

    jornada = AdmissaoJornada(
        employee_id=employee.id, obra=employee.obra, etapa=ADM_RASCUNHO
    )
    db.add(jornada)
    await db.commit()
    await db.refresh(jornada)
    await _auditar(db, jornada, "iniciada", actor=actor)
    return jornada


async def avancar(
    db: AsyncSession,
    jornada: AdmissaoJornada,
    *,
    actor: str = _AUDIT_ACTOR_SYSTEM,
    entregue_para: str | None = None,
) -> AdmissaoJornada:
    """Move para a proxima etapa SE os requisitos estiverem satisfeitos.

    Levanta `RequisitosNaoAtendidosError` com a lista do que falta --
    e o que torna a jornada ativa em vez de decorativa.
    """
    if jornada.etapa == ADM_CANCELADA:
        raise AdmissaoError("jornada cancelada nao avanca")
    alvo = _proxima_etapa(jornada.etapa)
    if alvo is None:
        raise AdmissaoError("jornada ja esta concluida")

    employee = await db.get(Employee, jornada.employee_id)
    if employee is None:
        raise AdmissaoError("funcionario nao encontrado")

    pend = await calcular_pendencias(db, employee)

    if alvo == ADM_DADOS_OK and pend.campos:
        raise RequisitosNaoAtendidosError(
            alvo, [f"campo ausente: {c}" for c in pend.campos]
        )
    if alvo == ADM_DOCS_OK and pend.documentos:
        raise RequisitosNaoAtendidosError(
            alvo, [f"documento ausente: {d}" for d in pend.documentos]
        )
    if alvo == ADM_KIT_GERADO:
        raise AdmissaoError(
            "kit nao se marca a mao -- use `gerar_kit` (o kit e o "
            "artefato, nao o status)"
        )
    if alvo == ADM_KIT_ENTREGUE:
        if not jornada.kit_path:
            raise RequisitosNaoAtendidosError(
                alvo, ["kit ainda nao foi gerado"]
            )
        jornada.kit_entregue_em = datetime.now(UTC)
        jornada.kit_entregue_para = entregue_para
    if alvo == ADM_CONCLUIDA:
        if not jornada.kit_entregue_em:
            raise RequisitosNaoAtendidosError(
                alvo, ["kit ainda nao foi entregue a contabilidade"]
            )
        jornada.confirmado_em = datetime.now(UTC)
        jornada.confirmado_por = actor

    jornada.etapa = alvo
    await db.commit()
    await db.refresh(jornada)
    await _auditar(db, jornada, f"avancou_para:{alvo}", actor=actor)
    return jornada


def montar_kit_csv(linhas: list[dict[str, Any]]) -> bytes:
    """Kit em CSV -- formato que a contabilidade abre e digita.

    CSV e nao PDF de proposito: a contabilidade COPIA os campos para o
    Dominio. Documento bonito para leitura seria pior para o trabalho
    que essa pessoa tem que fazer.
    """
    buffer = io.StringIO()
    colunas = [rotulo for _, rotulo in CAMPOS_OBRIGATORIOS] + [
        "Obra",
        "Documentos no dossie",
    ]
    escritor = csv.DictWriter(buffer, fieldnames=colunas, delimiter=";")
    escritor.writeheader()
    for linha in linhas:
        escritor.writerow(linha)
    # BOM: Excel em pt-BR abre UTF-8 sem ele como caractere quebrado.
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


async def linha_do_kit(
    db: AsyncSession, jornada: AdmissaoJornada
) -> dict[str, Any]:
    employee = await db.get(Employee, jornada.employee_id)
    if employee is None:
        raise AdmissaoError("funcionario nao encontrado")
    linha: dict[str, Any] = {}
    for campo, rotulo in CAMPOS_OBRIGATORIOS:
        valor = getattr(employee, campo, None)
        linha[rotulo] = valor.isoformat() if hasattr(valor, "isoformat") else (
            valor or ""
        )
    linha["Obra"] = jornada.obra or employee.obra or ""
    tipos = await _documentos_do_funcionario(db, employee.id)
    linha["Documentos no dossie"] = ", ".join(sorted(tipos)) or "—"
    return linha


async def gerar_kit(
    db: AsyncSession,
    jornada: AdmissaoJornada,
    *,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> tuple[AdmissaoJornada, bytes]:
    """Emite o kit de UMA admissao e avanca a etapa.

    Exige `documentos_ok`: kit gerado com pendencia vira retrabalho na
    contabilidade, que e justamente quem queremos poupar.
    """
    if jornada.etapa not in (ADM_DOCS_OK, ADM_KIT_GERADO):
        raise RequisitosNaoAtendidosError(
            ADM_KIT_GERADO,
            [f"etapa atual e '{jornada.etapa}', esperado 'documentos_ok'"],
        )
    conteudo = montar_kit_csv([await linha_do_kit(db, jornada)])
    jornada.kit_path = f"kit-admissao-{jornada.employee_id}.csv"
    jornada.kit_gerado_em = datetime.now(UTC)
    jornada.etapa = ADM_KIT_GERADO
    await db.commit()
    await db.refresh(jornada)
    await _auditar(db, jornada, "kit_gerado", actor=actor)
    return jornada, conteudo


async def gerar_kits_da_obra(
    db: AsyncSession,
    obra: str,
    *,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> tuple[list[AdmissaoJornada], bytes, list[dict[str, Any]]]:
    """Kit EM LOTE de uma obra -- um CSV com a leva inteira.

    E o caso que a sazonalidade impoe (ADR-003 D4): na abertura de uma
    frente de obra entram varias admissoes na mesma semana, e e esse
    pico que trava a contabilidade. Um kit por pessoa nao resolve pico.

    Quem ainda tem pendencia NAO entra no lote e volta na lista de
    ignorados, com o motivo -- silenciar isso mandaria admissao
    incompleta para a contabilidade.
    """
    jornadas = (
        (
            await db.execute(
                select(AdmissaoJornada)
                .where(AdmissaoJornada.obra == obra)
                .where(AdmissaoJornada.etapa.in_([ADM_DOCS_OK, ADM_KIT_GERADO]))
                .order_by(AdmissaoJornada.id)
            )
        )
        .scalars()
        .all()
    )

    incluidas: list[AdmissaoJornada] = []
    ignoradas: list[dict[str, Any]] = []
    linhas: list[dict[str, Any]] = []
    agora = datetime.now(UTC)

    for jornada in jornadas:
        employee = await db.get(Employee, jornada.employee_id)
        if employee is None:
            ignoradas.append(
                {"jornada_id": jornada.id, "motivo": "funcionario ausente"}
            )
            continue
        pend = await calcular_pendencias(db, employee)
        if not pend.vazio:
            ignoradas.append(
                {
                    "jornada_id": jornada.id,
                    "employee_id": employee.id,
                    "nome": employee.nome_completo,
                    "motivo": pend.como_lista(),
                }
            )
            continue
        linhas.append(await linha_do_kit(db, jornada))
        jornada.kit_path = f"kit-admissao-obra-{obra}.csv"
        jornada.kit_gerado_em = agora
        jornada.etapa = ADM_KIT_GERADO
        incluidas.append(jornada)

    await db.commit()
    conteudo = montar_kit_csv(linhas)
    db.add(
        AuditLog(
            actor=actor,
            action="kit_lote_gerado",
            resource=_AUDIT_RESOURCE,
            resource_id=obra,
            metadata_json=json.dumps(
                {"incluidas": len(incluidas), "ignoradas": len(ignoradas)}
            ),
        )
    )
    await db.commit()
    return incluidas, conteudo, ignoradas


async def linhas_do_lote(
    db: AsyncSession, obra: str
) -> list[dict[str, Any]]:
    """Relê o lote ja gerado de uma obra, SEM mudar etapa nenhuma.

    Baixar o arquivo de novo nao pode avancar a jornada de ninguem --
    seria um efeito colateral invisivel em cima de um GET.
    """
    jornadas = (
        (
            await db.execute(
                select(AdmissaoJornada)
                .where(AdmissaoJornada.obra == obra)
                .where(AdmissaoJornada.kit_gerado_em.isnot(None))
                .order_by(AdmissaoJornada.id)
            )
        )
        .scalars()
        .all()
    )
    return [await linha_do_kit(db, j) for j in jornadas]


async def painel(db: AsyncSession) -> dict[str, Any]:
    """Onde cada admissao parou, e o que a destrava.

    E a visao que faltava: sem ela, saber o andamento exigia abrir
    funcionario por funcionario.
    """
    jornadas = (
        (await db.execute(select(AdmissaoJornada).order_by(AdmissaoJornada.id)))
        .scalars()
        .all()
    )
    por_etapa: dict[str, int] = {e: 0 for e in ADMISSAO_ETAPAS}
    itens: list[dict[str, Any]] = []
    # Reflexo na OnSafety por pessoa, numa consulta so. "nunca" e um
    # estado de verdade -- diferente de "erro" e de "ok".
    from app.modules.dp_sesmt.onboarding import ultimo_run_por_employee

    reflexo = await ultimo_run_por_employee(db, [j.employee_id for j in jornadas])
    for jornada in jornadas:
        por_etapa[jornada.etapa] = por_etapa.get(jornada.etapa, 0) + 1
        employee = await db.get(Employee, jornada.employee_id)
        pend = (
            await calcular_pendencias(db, employee)
            if employee is not None
            else Pendencias()
        )
        itens.append(
            {
                "jornada_id": jornada.id,
                "employee_id": jornada.employee_id,
                "nome": employee.nome_completo if employee else "—",
                "obra": jornada.obra,
                "etapa": jornada.etapa,
                "pendencias": pend.como_lista(),
                "kit_gerado_em": jornada.kit_gerado_em,
                "kit_entregue_em": jornada.kit_entregue_em,
                "confirmado_em": jornada.confirmado_em,
                "onsafety": (
                    {
                        "status": run.status,
                        "em": run.executed_at.isoformat() if run.executed_at else None,
                        "erro": run.error_msg,
                    }
                    if (run := reflexo.get(jornada.employee_id)) is not None
                    else {"status": "nunca", "em": None, "erro": None}
                ),
            }
        )
    return {
        "total": len(jornadas),
        "por_etapa": por_etapa,
        "travadas": [i for i in itens if i["pendencias"]],
        "itens": itens,
    }


async def cancelar(
    db: AsyncSession,
    jornada: AdmissaoJornada,
    *,
    motivo: str,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> AdmissaoJornada:
    """Encerra a jornada sem concluir (desistencia, reprovacao no ASO).

    Nao apaga: a admissao cancelada e informacao -- saber que alguem
    entrou no processo e nao seguiu explica vaga em aberto e ASO pago.
    """
    if jornada.etapa == ADM_CONCLUIDA:
        raise AdmissaoError("jornada concluida nao se cancela")
    jornada.etapa = ADM_CANCELADA
    jornada.observacoes = motivo
    await db.commit()
    await db.refresh(jornada)
    await _auditar(db, jornada, "cancelada", actor=actor)
    return jornada


async def _auditar(
    db: AsyncSession,
    jornada: AdmissaoJornada,
    acao: str,
    *,
    actor: str,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=acao,
            resource=_AUDIT_RESOURCE,
            resource_id=str(jornada.id),
            metadata_json=json.dumps(
                {"employee_id": jornada.employee_id, "etapa": jornada.etapa}
            ),
        )
    )
    await db.commit()


__all__ = [
    "AdmissaoError",
    "CAMPOS_OBRIGATORIOS",
    "DOCUMENTOS_OBRIGATORIOS",
    "Pendencias",
    "RequisitosNaoAtendidosError",
    "avancar",
    "calcular_pendencias",
    "cancelar",
    "gerar_kit",
    "gerar_kits_da_obra",
    "iniciar",
    "linha_do_kit",
    "linhas_do_lote",
    "montar_kit_csv",
    "painel",
]
