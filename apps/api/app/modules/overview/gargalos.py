"""Gargalos por frente -- o que precisa de alguem hoje.

A home mostrava "Integracoes ativas" e um feed de automacoes: contava o
que o SISTEMA estava fazendo. Pedido de 21/09/2026: a home e para
mostrar onde a OPERACAO esta travada, por frente.

Tres regras:

- Gargalo so entra com quantidade > 0. Frente sem gargalo devolve lista
  vazia, e a tela diz "nada pendente" -- isso e informacao.
- Cada gargalo leva o link da tela que resolve. Numero sem caminho e
  ansiedade, nao acao.
- Nada de estado tecnico aqui (versao de servico, credencial, fila).
  Isso vive em /observability e nos cartoes de escopo de cada modulo.
  O que entra e o que uma pessoa da Primor reconhece como trabalho dela.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dp_sesmt.models import (
    ADM_CANCELADA,
    ADM_CONCLUIDA,
    AdmissaoJornada,
    Afastamento,
    Employee,
)
from app.modules.financeiro_contratos.models import Contrato
from app.modules.juridico.models import Andamento, Processo
from app.modules.licitacoes.models import CertidaoEmpresa, Licitacao
from app.modules.manutencao_frota import planos as planos_svc
from app.modules.ponto.models import PontoLocalTrabalho

CRITICO = "critico"
ATENCAO = "atencao"

# Passou disto sem licitacao nova, a captacao esta parada -- nao "sem
# novidade". Publicacao no PNCP e diaria em qualquer UF grande.
CAPTACAO_PARADA_APOS_DIAS = 7


def _g(
    chave: str, titulo: str, quantidade: int, gravidade: str, link: str,
    detalhe: str | None = None,
) -> dict[str, Any]:
    return {
        "chave": chave,
        "titulo": titulo,
        "quantidade": quantidade,
        "gravidade": gravidade,
        "link": link,
        "detalhe": detalhe,
    }


async def _count(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar() or 0)


async def _rh(db: AsyncSession, hoje: date, horizonte: date) -> list[dict]:
    itens = []
    ativo = Employee.status == "ativo"
    n = await _count(db, select(func.count(Employee.id)).where(
        and_(ativo, Employee.aso_validade.is_not(None), Employee.aso_validade < hoje)))
    if n:
        itens.append(_g("aso_vencido", "funcionário(s) com ASO vencido", n, CRITICO,
                        "/rh/funcionarios?aso=vencido",
                        "Sem ASO válido a pessoa não pode estar em campo."))
    n = await _count(db, select(func.count(Employee.id)).where(
        and_(ativo, Employee.aso_validade.is_not(None),
             Employee.aso_validade >= hoje, Employee.aso_validade <= horizonte)))
    if n:
        itens.append(_g("aso_vencendo", "ASO(s) vencendo em 30 dias", n, ATENCAO,
                        "/rh/funcionarios?aso=vencendo"))
    n = await _count(db, select(func.count(AdmissaoJornada.id)).where(
        AdmissaoJornada.etapa.notin_((ADM_CONCLUIDA, ADM_CANCELADA))))
    if n:
        itens.append(_g("admissoes_em_aberto", "admissão(ões) em andamento", n, ATENCAO,
                        "/rh/admissoes",
                        "Cada uma diz o que falta para avançar."))
    n = await _count(db, select(func.count(Afastamento.id)).where(
        Afastamento.data_retorno.is_(None)))
    if n:
        itens.append(_g("afastados", "funcionário(s) afastado(s) sem data de retorno", n,
                        ATENCAO, "/rh/afastamentos"))
    return itens


async def _frota(db: AsyncSession) -> list[dict]:
    itens = []
    painel = await planos_svc.status_dos_planos(db)
    vencidas = painel["por_status"].get("vencida", 0)
    if vencidas:
        itens.append(_g("revisoes_vencidas", "revisão(ões) de manutenção vencida(s)",
                        vencidas, CRITICO, "/manutencao/planos",
                        "Medido em horímetro/odômetro, não em calendário."))
    sem_leitura = painel["por_status"].get("sem_leitura", 0)
    if sem_leitura:
        itens.append(_g("planos_sem_leitura", "plano(s) sem leitura do equipamento",
                        sem_leitura, ATENCAO, "/manutencao/planos",
                        "Sem horímetro/odômetro não dá para saber se está em dia."))
    sem_plano = len(await planos_svc.veiculos_sem_plano(db))
    if sem_plano:
        itens.append(_g("sem_plano", "equipamento(s) sem plano de manutenção",
                        sem_plano, ATENCAO, "/manutencao/planos",
                        "“Nenhum alerta” não significa “em dia”."))
    return itens


async def _financeiro(db: AsyncSession, hoje: date, horizonte: date) -> list[dict]:
    itens = []
    ativo = Contrato.status != "encerrado"
    n = await _count(db, select(func.count(Contrato.id)).where(
        and_(ativo, Contrato.data_fim.is_not(None), Contrato.data_fim < hoje)))
    if n:
        itens.append(_g("contratos_vencidos", "contrato(s) vencido(s) e ainda ativo(s)",
                        n, CRITICO, "/financeiro/contratos"))
    n = await _count(db, select(func.count(Contrato.id)).where(
        and_(ativo, Contrato.data_fim.is_not(None),
             Contrato.data_fim >= hoje, Contrato.data_fim <= horizonte)))
    if n:
        itens.append(_g("contratos_vencendo", "contrato(s) vencendo em 30 dias", n,
                        ATENCAO, "/financeiro/contratos"))
    return itens


async def _licitacoes(db: AsyncSession, hoje: date, horizonte: date) -> list[dict]:
    itens = []
    n = await _count(db, select(func.count(CertidaoEmpresa.id)).where(
        and_(CertidaoEmpresa.validade.is_not(None), CertidaoEmpresa.validade < hoje)))
    if n:
        itens.append(_g("certidoes_vencidas", "certidão(ões) vencida(s)", n, CRITICO,
                        "/licitacoes/certidoes",
                        "Sem certidão válida a empresa não habilita em licitação."))
    n = await _count(db, select(func.count(CertidaoEmpresa.id)).where(
        and_(CertidaoEmpresa.validade.is_not(None),
             CertidaoEmpresa.validade >= hoje, CertidaoEmpresa.validade <= horizonte)))
    if n:
        itens.append(_g("certidoes_vencendo", "certidão(ões) vencendo em 30 dias", n,
                        ATENCAO, "/licitacoes/certidoes"))
    n = await _count(db, select(func.count(Licitacao.id)).where(
        Licitacao.status_triagem == "novo_captado"))
    if n:
        itens.append(_g("nao_triadas", "oportunidade(s) captada(s) sem triagem", n,
                        ATENCAO, "/licitacoes/triagem"))

    mais_nova = (await db.execute(select(func.max(Licitacao.data_publicacao_pncp)))).scalar()
    if mais_nova is not None:
        if mais_nova.tzinfo is None:
            mais_nova = mais_nova.replace(tzinfo=UTC)
        dias = (datetime.now(UTC) - mais_nova).days
        if dias > CAPTACAO_PARADA_APOS_DIAS:
            itens.append(_g("captacao_parada",
                            f"captação de licitações parada há {dias} dias", dias,
                            CRITICO, "/licitacoes",
                            "Nenhuma oportunidade nova desde a última carga."))
    return itens


async def _juridico(db: AsyncSession) -> list[dict]:
    itens = []
    n = await _count(db, select(func.count(Andamento.id)).where(
        Andamento.status == "PENDENTE"))
    if n:
        itens.append(_g("andamentos_pendentes", "andamento(s) pendente(s) no EasyJur", n,
                        ATENCAO, "/juridico",
                        "Marcados como pendentes pelo escritório."))
    n = await _count(db, select(func.count(Processo.id)).where(
        and_(Processo.codigo_obra.is_not(None), Processo.obra_id.is_(None))))
    if n:
        itens.append(_g("processos_obra_nao_cadastrada",
                        "processo(s) citando obra não cadastrada", n, ATENCAO, "/obras"))
    return itens


async def _obras(db: AsyncSession) -> list[dict]:
    itens = []
    # So local COM codigo conta: administrativo ("ADM PRIMOR") nao tem
    # obra por definicao -- e comportamento certo, nao pendencia.
    n = await _count(db, select(func.count(PontoLocalTrabalho.id)).where(
        and_(PontoLocalTrabalho.codigo_obra.is_not(None),
             PontoLocalTrabalho.obra_id.is_(None))))
    if n:
        itens.append(_g("obras_nao_cadastradas",
                        "local(is) de trabalho do ponto sem obra cadastrada", n, ATENCAO,
                        "/obras",
                        "O ponto já traz o código da obra; falta cadastrá-la aqui."))
    return itens


async def gargalos(db: AsyncSession) -> dict[str, Any]:
    hoje = date.today()
    horizonte = hoje + timedelta(days=30)
    frentes = [
        {"chave": "rh", "titulo": "RH / DP", "link": "/rh",
         "gargalos": await _rh(db, hoje, horizonte)},
        {"chave": "frota", "titulo": "Manutenção & Frota", "link": "/manutencao",
         "gargalos": await _frota(db)},
        {"chave": "financeiro", "titulo": "Financeiro & Contratos", "link": "/financeiro",
         "gargalos": await _financeiro(db, hoje, horizonte)},
        {"chave": "licitacoes", "titulo": "Licitações", "link": "/licitacoes",
         "gargalos": await _licitacoes(db, hoje, horizonte)},
        {"chave": "juridico", "titulo": "Jurídico", "link": "/juridico",
         "gargalos": await _juridico(db)},
        {"chave": "obras", "titulo": "Obras", "link": "/obras",
         "gargalos": await _obras(db)},
    ]
    for f in frentes:
        f["gargalos"].sort(key=lambda g: (g["gravidade"] != CRITICO, -g["quantidade"]))
        f["criticos"] = sum(1 for g in f["gargalos"] if g["gravidade"] == CRITICO)
    return {
        "gerado_em": datetime.now(UTC).isoformat(),
        "total": sum(len(f["gargalos"]) for f in frentes),
        "criticos": sum(f["criticos"] for f in frentes),
        "frentes": frentes,
    }
