"""Pull SST da OnSafety para o dossie do funcionario (Squad 2, ADR-001).

Traz ASOs (exames ocupacionais) e fichas de EPI da OnSafety e
materializa no dossie:

- ASO -> colunas `aso_data`/`aso_validade`/`aso_resultado` de
  `dp_employees`. Essas colunas alimentam os alertas A.2 EM PRODUCAO
  (cron 08h05) -- o pull roda 07h30 para os alertas usarem dado fresco.
- Fichas de EPI -> rows `EmployeeDocument` tipo `FICHA_EPI` com
  `source="onsafety"` e `onsafety_external_id` = id do controle na
  OnSafety (idempotencia: re-pull atualiza em vez de duplicar).
- Treinamentos de NR -> rows `EmployeeDocument` nos tipos
  `NR10`/`NR12`/`NR18`/`NR35`, mesma idempotencia. So entram
  treinamentos APROVADOS e COM VALIDADE conhecida (ver
  `pull_treinamentos`).
- `obra_id` do documento sai do `establishment` (Projeto) da OnSafety,
  resolvido contra `obras_obra`.

Regras de protecao:

- **Matching por CPF** (chave natural de `dp_employees`): o CPF vem
  normalizado do adapter (11 digitos) e passa por `is_valid_cpf` antes
  do lookup. Sem match -> contado em `*_no_match`, nunca cria
  funcionario (cadastro e responsabilidade do RH/onboarding).
- **ASO nunca regride**: so atualiza quando `data_aso` da OnSafety e
  mais recente que a atual do dossie (ou quando o dossie nao tem ASO).
  Dado manual mais novo nao e sobrescrito por pull atrasado.
- **LGPD**: cada funcionario tocado ganha uma row em
  `dp_dossie_consultas` (fonte `onsafety_aso`/`onsafety_epi`) -- ASO e
  dado de saude. Resumo do run vai para `audit_log`.
- Erros de upstream (OnsafetyError) NAO propagam: o summary volta com
  `error` preenchido e o que ja foi processado fica commitado.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.actors import SYSTEM as _AUDIT_ACTOR_SYSTEM
from app.audit.models import AuditLog
from app.core.cpf import is_valid_cpf
from app.integrations.onsafety.client import OnsafetyClient, OnsafetyError
from app.modules.dp_sesmt.models import (
    DOC_EMP_FICHA_EPI,
    DOC_EMP_NR10,
    DOC_EMP_NR12,
    DOC_EMP_NR18,
    DOC_EMP_NR35,
    DossieConsultaLog,
    Employee,
    EmployeeDocument,
)
from app.modules.obras.models import Obra

logger = logging.getLogger(__name__)

_AUDIT_RESOURCE = "dp_sesmt.onsafety_pull"
_PAGE_SIZE = 200

# `resultadoAso` e int32 SEM enum nem description no spec OpenAPI.
#
# Medido na base REAL em 11/09/2026 (401 fichas): o valor e SEMPRE 1
# (395x) ou nulo (5x) -- 2 e 3 nunca aparecem. Como praticamente todo
# ASO real e "apto", isso e evidencia forte de que **1 = apto**.
#
# 2 e 3 seguem SEM comprovacao, e ha motivo concreto para nao chutar:
# o `tipoExame` da mesma API e 0-indexado (0=Admissional, 1=Periodico,
# 3=Mudanca de risco). Se `resultadoAso` seguir a mesma convencao,
# "inapto" e "apto com restricoes" podem estar em ordem diferente da
# que assumiamos -- e trocar os dois e rotular errado dado de saude.
#
# Por isso mapeamos SO o 1. Qualquer outro valor vira a string crua do
# numero e conta em `aso_resultado_desconhecido`, para a UI exibir
# "desconhecido" em vez de um rotulo possivelmente invertido.
_RESULTADO_MAP = {1: "apto"}

# Treinamentos da OnSafety -> tipos de documento do dossie. So as NRs
# que o checklist do diagnostico conhece: um treinamento fora deste
# mapa (NR-06, integracao, brigada...) NAO vira documento -- ingerir
# como "OUTRO" encheria o checklist de ruido sem responder a nenhuma
# exigencia. Conta em `treinos_nr_desconhecida`.
_NR_DOC_POR_CODIGO = {
    "10": DOC_EMP_NR10,
    "12": DOC_EMP_NR12,
    "18": DOC_EMP_NR18,
    "35": DOC_EMP_NR35,
}
_RE_NR = re.compile(r"NR[\s.-]?0*(\d{1,2})", re.IGNORECASE)
# Codigo da obra embutido no nome do projeto ("OBRA 243 - GUAXIMA").
# Mesmo padrao dos locais de trabalho do Tangerino (ADR-003), usado so
# como fallback de `codigoExterno`.
_RE_CODIGO_OBRA = re.compile(r"\b(\d{2,6})\b")


@dataclass
class PullSummary:
    source: str = "onsafety"
    # ASO
    exames_total: int = 0
    aso_updated: int = 0
    aso_skipped_older: int = 0
    aso_sem_data: int = 0
    aso_no_match: int = 0
    aso_resultado_desconhecido: int = 0
    # EPI
    epis_total: int = 0
    epis_created: int = 0
    epis_updated: int = 0
    epis_no_match: int = 0
    # treinamentos (NR)
    treinos_total: int = 0
    treinos_created: int = 0
    treinos_updated: int = 0
    treinos_reprovados: int = 0
    treinos_sem_validade: int = 0
    treinos_nr_desconhecida: int = 0
    treinos_no_match: int = 0
    # obra do documento (establishment/Projeto da OnSafety)
    projeto_no_match: int = 0
    # geral
    cpfs_invalidos: int = 0
    datas_invalidas: int = 0
    error: str | None = None
    consultas_logadas: int = 0


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_date_counted(value: Any, summary: PullSummary) -> date | None:
    """Como `_parse_date`, mas conta string nao-vazia que falhou o parse.

    `validade` do controle de EPI e string LIVRE no spec (nao
    date-time) e nao foi validada em homolog (base sem EPIs). Se vier
    em formato BR ("30/05/2026"), sem este contador todas as fichas
    ficariam sem validade EM SILENCIO -- e o diagnostico documental
    deixaria de alertar vencimento.
    """
    parsed = _parse_date(value)
    if parsed is None and value:
        summary.datas_invalidas += 1
    return parsed


async def _iter_paginado(fetch: Any) -> list[dict[str, Any]]:
    """Percorre todas as paginas de um `list_*` do adapter."""
    items: list[dict[str, Any]] = []
    page = 0
    while True:
        res = await fetch(page=page, size=_PAGE_SIZE)
        items.extend(res["items"])
        if not res["items"] or len(items) >= res["total"]:
            return items
        page += 1


async def _log_consulta(
    db: AsyncSession,
    *,
    employee_id: int,
    fonte: str,
    cpf: str,
    ja_logados: set[tuple[int, str]],
) -> bool:
    """Uma row LGPD por (employee, fonte) por run -- nao por item."""
    key = (employee_id, fonte)
    if key in ja_logados:
        return False
    ja_logados.add(key)
    db.add(
        DossieConsultaLog(
            employee_id=employee_id,
            fonte=fonte,
            chave_consulta=cpf,
            sucesso=True,
        )
    )
    return True


@dataclass
class _Indices:
    """Tudo que o pull precisa consultar, em 3 SELECTs.

    Antes era 1 SELECT por item (funcionario por CPF + documento por
    external id); com a base de producao (~5.3k trabalhadores e varios
    itens por trabalhador) isso dominava o tempo do run. Follow-up de
    performance registrado na issue #39.
    """

    employees: dict[str, Employee]
    docs: dict[str, EmployeeDocument]
    obras: dict[str, Obra]


async def carregar_indices(db: AsyncSession) -> _Indices:
    employees = {
        e.cpf: e
        for e in (await db.execute(select(Employee))).scalars().all()
        if e.cpf
    }
    docs = {
        d.onsafety_external_id: d
        for d in (
            await db.execute(
                select(EmployeeDocument).where(
                    EmployeeDocument.onsafety_external_id.is_not(None)
                )
            )
        )
        .scalars()
        .all()
        if d.onsafety_external_id
    }
    obras = {
        o.codigo.strip(): o
        for o in (await db.execute(select(Obra))).scalars().all()
        if o.codigo
    }
    return _Indices(employees=employees, docs=docs, obras=obras)


def _match_employee(
    indices: _Indices, item: dict[str, Any]
) -> tuple[Employee | None, str | None]:
    """Devolve (employee, motivo_da_falha).

    Motivos disjuntos -- `cpfs_invalidos` e `*_no_match` do summary
    NAO se sobrepoem: "invalido" = CPF reprovado em is_valid_cpf;
    "no_match" = CPF valido sem funcionario correspondente.
    """
    trabalhador = item.get("trabalhador") or {}
    cpf = trabalhador.get("cpf") or ""
    if not is_valid_cpf(cpf):
        return None, "invalido"
    employee = indices.employees.get(cpf)
    if employee is None:
        return None, "no_match"
    return employee, None


def _resolver_obra(
    indices: _Indices,
    projeto: dict[str, Any] | None,
    summary: PullSummary,
) -> int | None:
    """Projeto da OnSafety -> `obras_obra.id`.

    `codigoExterno` primeiro (canal explicito de reconciliacao); se
    vier vazio, tenta o codigo embutido no nome ("OBRA 243 - ...") como
    a integracao do Tangerino faz (ADR-003). Sem match nao bloqueia a
    ingestao -- o documento entra com `obra_id` nulo e o contador
    `projeto_no_match` mostra o tamanho do buraco de cadastro.
    """
    if not projeto:
        return None
    codigo = (projeto.get("codigo_externo") or "").strip()
    obra = indices.obras.get(codigo) if codigo else None
    if obra is None:
        achado = _RE_CODIGO_OBRA.search(projeto.get("nome") or "")
        if achado:
            obra = indices.obras.get(achado.group(1))
    if obra is None:
        summary.projeto_no_match += 1
        return None
    return obra.id


def _tipo_doc_nr(item: dict[str, Any]) -> str | None:
    """Descobre a NR do treinamento: `grupo` -> `sigla` -> `descricao`.

    `grupo` do treinamentoCodigo e o rotulo normalizado da OnSafety; os
    outros dois sao texto livre e ficam como fallback.
    """
    for campo in ("grupo", "sigla", "descricao"):
        achado = _RE_NR.search(item.get(campo) or "")
        if achado:
            tipo = _NR_DOC_POR_CODIGO.get(achado.group(1).lstrip("0"))
            if tipo:
                return tipo
    return None


async def pull_asos(
    db: AsyncSession,
    client: OnsafetyClient,
    summary: PullSummary,
    ja_logados: set[tuple[int, str]],
    indices: _Indices,
) -> None:
    exames = await _iter_paginado(client.list_exames_ocupacionais)
    summary.exames_total = len(exames)
    for exame in exames:
        employee, motivo = _match_employee(indices, exame)
        if employee is None:
            if motivo == "invalido":
                summary.cpfs_invalidos += 1
            else:
                summary.aso_no_match += 1
            continue
        if await _log_consulta(
            db,
            employee_id=employee.id,
            fonte="onsafety_aso",
            cpf=(exame.get("trabalhador") or {}).get("cpf") or "",
            ja_logados=ja_logados,
        ):
            summary.consultas_logadas += 1

        data_aso = _parse_date_counted(exame.get("data_aso"), summary)
        if data_aso is None:
            summary.aso_sem_data += 1
            continue
        # ASO nunca regride: pull atrasado nao sobrescreve dado manual
        # (ou de pull anterior) mais recente.
        if employee.aso_data is not None and data_aso <= employee.aso_data:
            summary.aso_skipped_older += 1
            continue

        resultado_raw = exame.get("resultado_aso")
        resultado = _RESULTADO_MAP.get(resultado_raw)
        if resultado is None and resultado_raw is not None:
            summary.aso_resultado_desconhecido += 1
            resultado = str(resultado_raw)[:16]

        employee.aso_data = data_aso
        employee.aso_validade = _parse_date_counted(
            exame.get("data_vencimento_aso"), summary
        )
        employee.aso_resultado = resultado
        summary.aso_updated += 1
    await db.commit()


async def pull_epis(
    db: AsyncSession,
    client: OnsafetyClient,
    summary: PullSummary,
    ja_logados: set[tuple[int, str]],
    indices: _Indices,
) -> None:
    controles = await _iter_paginado(client.list_controles_epi)
    summary.epis_total = len(controles)
    for controle in controles:
        employee, motivo = _match_employee(indices, controle)
        if employee is None:
            if motivo == "invalido":
                summary.cpfs_invalidos += 1
            else:
                summary.epis_no_match += 1
            continue
        if await _log_consulta(
            db,
            employee_id=employee.id,
            fonte="onsafety_epi",
            cpf=(controle.get("trabalhador") or {}).get("cpf") or "",
            ja_logados=ja_logados,
        ):
            summary.consultas_logadas += 1

        external_id = str(controle.get("id"))
        doc = indices.docs.get(external_id)

        ca = controle.get("ca")
        quantidade = controle.get("quantidade")
        observacoes = (
            f"{controle.get('nome_equipamento') or 'EPI'}"
            f" (qtd {quantidade})" if quantidade is not None else ""
        ) or None
        fields = {
            "employee_id": employee.id,
            "tipo": DOC_EMP_FICHA_EPI,
            "numero": f"CA {ca}" if ca is not None else None,
            "emissao": _parse_date_counted(
                controle.get("data_entrega"), summary
            ),
            "validade": _parse_date_counted(
                controle.get("validade"), summary
            ),
            "observacoes": observacoes,
            "source": "onsafety",
            "onsafety_external_id": external_id,
            "obra_id": _resolver_obra(
                indices, controle.get("projeto"), summary
            ),
        }
        if doc is None:
            novo = EmployeeDocument(**fields)
            db.add(novo)
            indices.docs[external_id] = novo
            summary.epis_created += 1
        else:
            # Docs do sync sao espelho da OnSafety -- atualizamos in
            # place (rows manuais, source="manual", nunca sao tocadas
            # porque nao tem onsafety_external_id).
            for k, v in fields.items():
                setattr(doc, k, v)
            summary.epis_updated += 1
    await db.commit()


async def pull_treinamentos(
    db: AsyncSession,
    client: OnsafetyClient,
    summary: PullSummary,
    ja_logados: set[tuple[int, str]],
    indices: _Indices,
) -> None:
    """Treinamentos de NR -> documentos `NR10`/`NR12`/`NR18`/`NR35`.

    Percorre `/v2/treinamentos_realizados` e desce nos participantes.
    O caminho inverso (`.../trabalhadores`) NAO serve: contra a base
    real a relacao `treinamentoRealizado` volta vazia, entao sigla,
    descricao e validade nunca chegariam (medido em 11/09/2026).

    Dois filtros deliberados, ambos para nao produzir falso
    "conforme" no diagnostico documental:

    1. **So participante aprovado.** Reprovado nao e comprovante de
       capacitacao; entra so no contador.
    2. **So treinamento com validade conhecida.** `validade=None`
       significa "documento perene, sem prazo" para
       `diagnostico/runner.py` -- uma NR-35 vencida gravada sem
       validade apareceria como OK, que e pior do que aparecer
       ausente. Vencimento vem de `data_vencimento`; sem ele, de
       `data_fim + validade_dias`.

    A NR sai da `sigla` ("NR 35"); na base real o `grupo` e uma
    categoria descritiva, nao o rotulo da norma. NR fora do checklist
    (NR-6, sinalizacao viaria, brigada...) nao vira documento.
    """
    treinos = await _iter_paginado(client.list_treinamentos_realizados)
    summary.treinos_total = len(treinos)
    for treino in treinos:
        tipo = _tipo_doc_nr(treino)
        emissao = _parse_date_counted(treino.get("data_fim"), summary)
        validade = _parse_date_counted(treino.get("data_vencimento"), summary)
        if validade is None:
            dias = treino.get("validade_dias")
            if emissao is not None and isinstance(dias, int) and dias > 0:
                validade = emissao + timedelta(days=dias)
        obra_id = _resolver_obra(indices, treino.get("projeto"), summary)

        for participante in treino.get("participantes") or []:
            employee, motivo = _match_employee(indices, participante)
            if employee is None:
                if motivo == "invalido":
                    summary.cpfs_invalidos += 1
                else:
                    summary.treinos_no_match += 1
                continue
            if await _log_consulta(
                db,
                employee_id=employee.id,
                fonte="onsafety_treinamento",
                cpf=(participante.get("trabalhador") or {}).get("cpf") or "",
                ja_logados=ja_logados,
            ):
                summary.consultas_logadas += 1

            if not participante.get("aprovado"):
                summary.treinos_reprovados += 1
                continue
            if tipo is None:
                summary.treinos_nr_desconhecida += 1
                continue
            if validade is None:
                summary.treinos_sem_validade += 1
                continue

            external_id = str(participante.get("id"))
            doc = indices.docs.get(external_id)
            certificado = participante.get("certificado_id")
            fields = {
                "employee_id": employee.id,
                "tipo": tipo,
                "numero": str(certificado)[:128] if certificado else None,
                "emissao": emissao,
                "validade": validade,
                "observacoes": treino.get("descricao") or None,
                "source": "onsafety",
                "onsafety_external_id": external_id,
                "obra_id": obra_id,
            }
            if doc is None:
                novo_doc = EmployeeDocument(**fields)
                db.add(novo_doc)
                indices.docs[external_id] = novo_doc
                summary.treinos_created += 1
            else:
                for k, v in fields.items():
                    setattr(doc, k, v)
                summary.treinos_updated += 1
    await db.commit()


async def pull_onsafety(
    db: AsyncSession,
    client: OnsafetyClient,
    *,
    actor: str = _AUDIT_ACTOR_SYSTEM,
) -> PullSummary:
    """Pull completo (ASOs + EPIs + treinamentos). Nunca propaga
    OnsafetyError."""
    summary = PullSummary()
    ja_logados: set[tuple[int, str]] = set()
    indices = await carregar_indices(db)
    try:
        await pull_asos(db, client, summary, ja_logados, indices)
        await pull_epis(db, client, summary, ja_logados, indices)
        await pull_treinamentos(db, client, summary, ja_logados, indices)
    except OnsafetyError as exc:
        logger.warning("pull onsafety interrompido: %s", exc)
        summary.error = str(exc)[:500]
        await db.commit()  # preserva o que ja foi processado

    db.add(
        AuditLog(
            actor=actor,
            action="pull" if summary.error is None else "error",
            resource=_AUDIT_RESOURCE,
            resource_id=None,
            metadata_json=json.dumps(asdict(summary), default=str),
        )
    )
    await db.commit()
    return summary
