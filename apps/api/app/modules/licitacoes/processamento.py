"""Processamento pos-aprovacao do Captador (Squad 2).

Maquina de status da triagem (Projeto Tecnico, secao 5.1):

    novo_captado -> em_analise -> aprovado    (Squad 1, Tela de Captacao)
    aprovado -> processando_anexos -> completo | sem_planilha
                                   -> erro_portal | erro_sharepoint

`processar_aprovado` e idempotente: pode re-rodar sobre completo /
sem_planilha / erro_* (retry manual) sem duplicar pasta nem planilhas.
Estados que NUNCA processam: novo_captado, em_analise, rejeitado.
"""
from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import PncpClient
from app.modules.licitacoes.editais import (
    download_edital_for_licitacao,
    get_edital,
    list_anexos,
)
from app.modules.licitacoes.identificacao_planilha import (
    LIMIAR_PRINCIPAL,
    classificar_anexo,
    normalizar,
)
from app.modules.licitacoes.models import (
    Licitacao,
    PastaProjeto,
    PlanilhaOrcamentaria,
)
from app.modules.licitacoes.schemas import ProcessamentoResult
from app.modules.licitacoes.storage import EditaisStorage

logger = logging.getLogger(__name__)

STATUS_NOVO_CAPTADO = "novo_captado"
STATUS_EM_ANALISE = "em_analise"
STATUS_APROVADO = "aprovado"
STATUS_REJEITADO = "rejeitado"
STATUS_PROCESSANDO_ANEXOS = "processando_anexos"
STATUS_COMPLETO = "completo"
STATUS_SEM_PLANILHA = "sem_planilha"
STATUS_ERRO_PORTAL = "erro_portal"
STATUS_ERRO_SHAREPOINT = "erro_sharepoint"

_PROCESSAVEIS = frozenset(
    {
        STATUS_APROVADO,
        STATUS_PROCESSANDO_ANEXOS,
        STATUS_COMPLETO,
        STATUS_SEM_PLANILHA,
        STATUS_ERRO_PORTAL,
        STATUS_ERRO_SHAREPOINT,
    }
)

_PASTA_SAFE = re.compile(r"[^a-z0-9._-]+")


class ProcessamentoNaoPermitido(RuntimeError):
    """Licitacao nao esta num status que permita processar anexos."""


def montar_nome_pasta(licitacao: Licitacao) -> str:
    """Slug humano `<uf>-<municipio>-<orgao>-<numero>` para exibicao."""
    partes = [
        licitacao.uf_sigla or "uf",
        licitacao.municipio_nome or "municipio",
        licitacao.orgao_razao_social or "orgao",
        licitacao.numero_compra or str(licitacao.id),
    ]
    segmentos = [
        _PASTA_SAFE.sub("_", normalizar(p)).strip("_") or "x" for p in partes
    ]
    return "-".join(segmentos)[:120]


async def processar_aprovado(
    db: AsyncSession,
    *,
    licitacao_id: int,
    pncp: PncpClient,
    storage: EditaisStorage,
) -> ProcessamentoResult:
    licitacao = await db.get(Licitacao, licitacao_id)
    if licitacao is None:
        raise ValueError(f"Licitacao {licitacao_id} not found")
    if licitacao.status_triagem not in _PROCESSAVEIS:
        raise ProcessamentoNaoPermitido(
            f"status_triagem={licitacao.status_triagem!r} nao permite processar"
        )

    licitacao.status_triagem = STATUS_PROCESSANDO_ANEXOS
    await db.commit()

    # 1. Anexos (reusa D.4, idempotente).
    try:
        download = await download_edital_for_licitacao(
            db, licitacao_id=licitacao_id, pncp=pncp, storage=storage
        )
    except ValueError as exc:
        # Triplet PNCP (cnpj/ano/sequencial) ausente ou invalido: nao e um
        # erro de programacao, e a licitacao que nao pode ser processada
        # neste portal -- vira erro_portal em vez de vazar 500/404.
        logger.warning(
            "triplet PNCP ausente/invalido para licitacao %s: %s", licitacao_id, exc
        )
        licitacao.status_triagem = STATUS_ERRO_PORTAL
        await db.commit()
        return ProcessamentoResult(
            licitacao_id=licitacao_id,
            status_triagem=STATUS_ERRO_PORTAL,
            anexos_count=0,
            error_message=str(exc),
        )
    if download.status == "failed":
        licitacao.status_triagem = STATUS_ERRO_PORTAL
        await db.commit()
        return ProcessamentoResult(
            licitacao_id=licitacao_id,
            status_triagem=STATUS_ERRO_PORTAL,
            anexos_count=download.anexos_count,
            error_message=download.error_message or "download falhou",
        )

    # 2. Pasta do projeto.
    try:
        caminho, link = await storage.ensure_project_folder(
            licitacao_id=licitacao_id, nome_pasta=montar_nome_pasta(licitacao)
        )
    except OSError as exc:
        logger.warning("pasta do projeto falhou (lic %s): %s", licitacao_id, exc)
        licitacao.status_triagem = STATUS_ERRO_SHAREPOINT
        await db.commit()
        return ProcessamentoResult(
            licitacao_id=licitacao_id,
            status_triagem=STATUS_ERRO_SHAREPOINT,
            anexos_count=download.anexos_count,
            error_message=str(exc),
        )
    await _upsert_pasta(
        db, licitacao=licitacao, caminho=caminho, link=link, storage=storage
    )

    # 3. Classificacao dos anexos + planilha principal.
    principal_anexo_id = await _classificar_planilhas(
        db, licitacao_id=licitacao_id, storage=storage
    )

    licitacao.status_triagem = (
        STATUS_COMPLETO if principal_anexo_id is not None else STATUS_SEM_PLANILHA
    )
    await db.commit()
    pasta_link = link
    return ProcessamentoResult(
        licitacao_id=licitacao_id,
        status_triagem=licitacao.status_triagem,
        anexos_count=download.anexos_count,
        planilha_encontrada=principal_anexo_id is not None,
        planilha_anexo_id=principal_anexo_id,
        pasta_link=pasta_link,
    )


async def _upsert_pasta(
    db: AsyncSession,
    *,
    licitacao: Licitacao,
    caminho: str,
    link: str | None,
    storage: EditaisStorage,
) -> None:
    stmt = select(PastaProjeto).where(PastaProjeto.licitacao_id == licitacao.id)
    pasta = (await db.execute(stmt)).scalar_one_or_none()
    backend = type(storage).__name__.replace("Storage", "").lower() or "local"
    if pasta is None:
        pasta = PastaProjeto(
            licitacao_id=licitacao.id,
            nome_pasta=montar_nome_pasta(licitacao),
            caminho=caminho,
            link_pasta=link,
            storage_backend=backend,
        )
        db.add(pasta)
    else:
        pasta.caminho = caminho
        pasta.link_pasta = link
        pasta.storage_backend = backend
        pasta.status = "criada"
    await db.flush()


async def _classificar_planilhas(
    db: AsyncSession, *, licitacao_id: int, storage: EditaisStorage
) -> int | None:
    """Upsert de `PlanilhaOrcamentaria` por anexo elegivel.

    Retorna o `anexo_id` da planilha principal, ou None. Escolha manual
    previa (`status_validacao=principal_manual`) e respeitada.
    """
    edital = await get_edital(db, licitacao_id)
    if edital is None:
        return None
    anexos = await list_anexos(db, edital.id)

    stmt = select(PlanilhaOrcamentaria).where(
        PlanilhaOrcamentaria.licitacao_id == licitacao_id
    )
    existentes = {
        p.anexo_id: p for p in (await db.execute(stmt)).scalars().all()
    }

    candidatas: list[PlanilhaOrcamentaria] = []
    for anexo in anexos:
        extensao = PurePosixPath(anexo.filename).suffix.lower()
        conteudo: bytes | None = None
        leitura_falhou = False
        if extensao in {".xlsx", ".ods"}:
            try:
                conteudo = await storage.read(anexo.storage_path)
            except (OSError, FileNotFoundError) as exc:
                leitura_falhou = True
                logger.warning(
                    "leitura do anexo %s falhou, score so por nome: %s",
                    anexo.id,
                    exc,
                )
        score = classificar_anexo(anexo.filename, conteudo)
        if score is None:
            continue
        row = existentes.get(anexo.id)
        if row is None:
            row = PlanilhaOrcamentaria(
                licitacao_id=licitacao_id,
                anexo_id=anexo.id,
                nome_arquivo=anexo.filename,
                extensao=extensao,
                score_classificacao=score,
                link=anexo.source_url,
            )
            db.add(row)
        else:
            if leitura_falhou:
                # Arquivo sumiu do storage (nao e extensao inelegivel): nao
                # rebaixa uma classificacao ja feita com o conteudo em maos.
                row.score_classificacao = max(score, row.score_classificacao or 0)
            else:
                row.score_classificacao = score
            row.link = anexo.source_url
        candidatas.append(row)
    await db.flush()

    if not candidatas:
        return None

    manual = next(
        (c for c in candidatas if c.status_validacao == "principal_manual"), None
    )
    if manual is not None:
        principal = manual
    else:
        melhor = max(candidatas, key=lambda c: c.score_classificacao)
        principal = melhor if melhor.score_classificacao >= LIMIAR_PRINCIPAL else None

    for c in candidatas:
        c.principal = principal is not None and c.id == principal.id
    await db.flush()
    return principal.anexo_id if principal is not None else None


async def marcar_planilha_principal(
    db: AsyncSession, *, licitacao_id: int, planilha_id: int
) -> PlanilhaOrcamentaria:
    """Analista elege manualmente outra planilha como principal."""
    stmt = select(PlanilhaOrcamentaria).where(
        PlanilhaOrcamentaria.licitacao_id == licitacao_id
    )
    rows = list((await db.execute(stmt)).scalars().all())
    alvo = next((r for r in rows if r.id == planilha_id), None)
    if alvo is None:
        raise ValueError(
            f"Planilha {planilha_id} nao encontrada para licitacao {licitacao_id}"
        )
    for r in rows:
        r.principal = r.id == planilha_id
        if r.status_validacao == "principal_manual" and r.id != planilha_id:
            r.status_validacao = "automatica"
    alvo.status_validacao = "principal_manual"

    licitacao = await db.get(Licitacao, licitacao_id)
    if licitacao is not None and licitacao.status_triagem == STATUS_SEM_PLANILHA:
        licitacao.status_triagem = STATUS_COMPLETO
    await db.commit()
    await db.refresh(alvo)
    return alvo
