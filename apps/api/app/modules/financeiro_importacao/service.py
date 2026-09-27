"""Carga em staging, conferência e troca atômica da base financeira ativa."""

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.modules.financeiro_importacao.models import (
    ApropriacaoFinanceira as Apropriacao,
)
from app.modules.financeiro_importacao.models import BaseFinanceira, ImportacaoFinanceira
from app.modules.financeiro_importacao.parser import CAMPOS, ler_planilha
from app.modules.financeiro_importacao.schemas import Filtros
from app.modules.licitacoes.storage import EditaisStorage
from app.modules.manutencao_frota.service import get_celery_dispatcher

logger = logging.getLogger(__name__)
TASK = "worker.tasks.financeiro_importacao.validar"


def auditar(db: AsyncSession, lote_id: int, actor: str, action: str, **metadata) -> None:
    """Mantém a auditoria na mesma transação da mudança."""
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource="financeiro.importacao",
            resource_id=str(lote_id),
            metadata_json=json.dumps(metadata),
        )
    )


async def base_ativa(db: AsyncSession, *, lock: bool = False) -> BaseFinanceira:
    """A migration cria o singleton; o fallback atende instalações via create_all."""
    if await db.get(BaseFinanceira, 1) is None:
        try:
            async with db.begin_nested():
                db.add(BaseFinanceira(id=1))
                await db.flush()
        except IntegrityError:
            pass
    query = select(BaseFinanceira).where(BaseFinanceira.id == 1)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one()


async def preparar(db: AsyncSession, *, nome: str, sha256: str, actor: str) -> ImportacaoFinanceira:
    """O mesmo conteúdo retoma o lote existente, inclusive após falha de fila."""
    lote = (
        await db.execute(select(ImportacaoFinanceira).where(ImportacaoFinanceira.sha256 == sha256))
    ).scalar_one_or_none()
    if lote:
        return lote
    lote = ImportacaoFinanceira(nome_arquivo=nome, sha256=sha256, actor=actor)
    try:
        async with db.begin_nested():
            db.add(lote)
            await db.flush()
    except IntegrityError:
        return (
            await db.execute(
                select(ImportacaoFinanceira).where(ImportacaoFinanceira.sha256 == sha256)
            )
        ).scalar_one()
    auditar(db, lote.id, actor, "create")
    await db.commit()
    return lote


async def salvar_arquivo(storage: EditaisStorage, lote_id: int, suffix: str, content: bytes) -> str:
    """Usa o storage compartilhado da API/worker com nome isolado de editais."""

    async def chunks():
        yield content

    path, _ = await storage.save(
        licitacao_id=0, filename=f"financeiro-{lote_id}-{suffix}.xlsx", content=chunks()
    )
    return path


async def enfileirar(db: AsyncSession, lote: ImportacaoFinanceira, actor: str) -> None:
    """Estado persistido antes do despacho; falha de fila pode ser retomada."""
    lote.status = "na_fila"
    lote.mensagem = None
    auditar(db, lote.id, actor, "enqueue")
    await db.commit()
    try:
        await asyncio.to_thread(
            get_celery_dispatcher().send_task, TASK, args=[lote.id], queue="financeiro"
        )
    except Exception as exc:
        lote.status = "erro_fila"
        lote.mensagem = "Não foi possível enfileirar. Tente novamente."
        auditar(db, lote.id, actor, "error", etapa="fila")
        await db.commit()
        logger.exception("financeiro.falha_fila lote=%s", lote.id)
        raise RuntimeError(lote.mensagem) from exc


def _append_export(sheet, record: dict) -> None:
    values = [record[campo] for campo in CAMPOS.values()]
    values += [record["competencia"], record["classificacao"]]
    cells = []
    for value in values:
        cell = WriteOnlyCell(sheet, value=value)
        if isinstance(value, str):
            cell.data_type = "s"  # nomes/títulos iniciados por '=' nunca viram fórmula
        cells.append(cell)
    sheet.append(cells)


async def processar(db: AsyncSession, storage: EditaisStorage, lote_id: int) -> None:
    """Executado apenas no worker; lock de linha impede entregas Celery concorrentes."""
    lote = (
        await db.execute(
            select(ImportacaoFinanceira).where(ImportacaoFinanceira.id == lote_id).with_for_update()
        )
    ).scalar_one()
    if lote.status not in ("na_fila", "erro_processamento"):
        return
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Relatório Completo")
    sheet.append([*CAMPOS, "COMPETÊNCIA DA EMISSÃO", "CLASSIFICAÇÃO"])
    try:
        content = await storage.read(lote.storage_path)
        if hashlib.sha256(content).hexdigest() != lote.sha256:
            raise ValueError("Arquivo armazenado diverge do hash informado")
        await db.execute(delete(Apropriacao).where(Apropriacao.lote_id == lote.id))
        totais = defaultdict(lambda: {"linhas": 0, "valor": Decimal(0)})
        batch = []
        lote.linhas = lote.erros = lote.avisos = 0
        ocorrencias = []
        for linha, record, avisos in ler_planilha(content):
            lote.linhas += 1
            if record is None:
                lote.erros += 1
            elif avisos:
                lote.avisos += 1
            for aviso in avisos:
                if len(ocorrencias) < 100:
                    ocorrencias.append(
                        {
                            "linha": linha,
                            "tipo": "erro" if record is None else "aviso",
                            "mensagem": aviso,
                        }
                    )
            if record is None:
                continue
            totais[record["empresa"]]["linhas"] += 1
            totais[record["empresa"]]["valor"] += record["valor_apropriado"]
            _append_export(sheet, record)
            batch.append({**record, "lote_id": lote.id, "linha": linha})
            if len(batch) == 500:
                await db.execute(insert(Apropriacao), batch)
                batch.clear()
        if batch:
            await db.execute(insert(Apropriacao), batch)
        if not lote.linhas:
            raise ValueError("Planilha sem registros")
        lote.ocorrencias = ocorrencias
        lote.resumo = [
            {
                "empresa": empresa,
                "linhas": values["linhas"],
                "valor_apropriado": str(values["valor"]),
            }
            for empresa, values in sorted(totais.items())
        ]
        output = BytesIO()
        workbook.save(output)
        # Arquivo inválido não ganha exportação aparentemente completa.
        lote.export_path = None
        if not lote.erros:
            lote.export_path = await salvar_arquivo(
                storage, lote.id, "conferido", output.getvalue()
            )
        lote.status = "invalido" if lote.erros else "validado"
        lote.mensagem = None
        auditar(db, lote.id, "system:worker", "validate", erros=lote.erros, linhas=lote.linhas)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        lote = await db.get(ImportacaoFinanceira, lote_id)
        lote.status = "erro_processamento"
        lote.mensagem = (
            str(exc)[:500]
            if isinstance(exc, ValueError)
            else "Falha no processamento; consulte os logs do worker."
        )
        auditar(db, lote_id, "system:worker", "error", etapa="processamento")
        await db.commit()
        raise
    finally:
        # Fecha o gerador XML também quando a validação falha antes de save().
        if not sheet.closed:
            sheet.close()
        workbook.close()


async def confirmar(db: AsyncSession, lote_id: int, substituir: int | None, actor: str) -> None:
    """Troca explícita e atômica, preservando todo o histórico anterior."""
    base = await base_ativa(db, lock=True)
    lote = await db.get(ImportacaoFinanceira, lote_id)
    if base.lote_id == lote_id:
        return
    if base.lote_id != substituir:
        raise ValueError("A base ativa mudou. Atualize a tela e confira a substituição.")
    if not lote or lote.status not in ("validado", "historico") or lote.erros:
        raise ValueError("Somente uma planilha validada sem erros pode ser confirmada.")
    if base.lote_id:
        anterior = await db.get(ImportacaoFinanceira, base.lote_id)
        anterior.status = "historico"
    base.lote_id = lote_id
    lote.status = "ativo"
    auditar(db, lote_id, actor, "confirm", substitui=substituir)
    await db.commit()


def consulta(lote_id: int | None, filtros: Filtros):
    """Filtros sobre apropriações de uma única versão, sem somar versões históricas."""
    q = select(Apropriacao).where(Apropriacao.lote_id == (lote_id or -1))
    for field in (
        "empresa",
        "local",
        "centro_custo",
        "natureza_i",
        "natureza_ii",
        "natureza_iii",
        "classificacao",
    ):
        if value := getattr(filtros, field):
            q = q.where(getattr(Apropriacao, field) == value)
    for field in ("contraparte", "natureza"):
        if value := getattr(filtros, field):
            q = q.where(getattr(Apropriacao, field).icontains(value, autoescape=True))
    column = getattr(Apropriacao, filtros.campo_data)
    if filtros.inicio:
        q = q.where(column >= filtros.inicio)
    if filtros.fim:
        q = q.where(column <= filtros.fim)
    return q


async def listar(db: AsyncSession, lote_id: int | None, filtros: Filtros) -> dict:
    """Retorna paginação e total monetário apenas de valores apropriados."""
    if filtros.inicio and filtros.fim and filtros.inicio > filtros.fim:
        raise ValueError("Data inicial deve ser anterior à data final")
    q = consulta(lote_id, filtros)
    sub = q.subquery()
    total, valor = (await db.execute(select(func.count(), func.sum(sub.c.valor_apropriado)))).one()
    rows = (
        (
            await db.execute(
                q.order_by(Apropriacao.linha)
                .offset((filtros.page - 1) * filtros.page_size)
                .limit(filtros.page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total": total,
        "valor_apropriado": str(valor or 0),
        "lote_id": lote_id,
        "data": [
            {
                field: getattr(row, field)
                for field in [*CAMPOS.values(), "linha", "competencia", "classificacao"]
            }
            for row in rows
        ],
    }
