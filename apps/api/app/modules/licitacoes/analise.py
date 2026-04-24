"""Edital LLM analysis service (D.5).

Given a `Licitacao.id`, reads the text from every downloaded anexo
(D.4 wrote them to disk), feeds it to an `LLMProvider` and persists the
structured result into `licitacoes_editais_analises`.

Re-running against the same edital is idempotent: the single row for
that edital is updated in place.

The prompt / schema is intentionally small and focused on fields that
matter for "should we bid?" decisions on heavy construction contracts:

* prazo_execucao_dias       -- how long we have to deliver
* garantia_percentual       -- required bid/contract guarantee (%)
* bdi_maximo_percentual     -- max allowed BDI the edital imposes
* atestados_cat             -- list of CAT certifications required
* visita_tecnica_obrigatoria
* valor_estimado            -- valor de referencia
* observacoes               -- free-form summary of risks / gotchas

Fields are optional: the schema marks them as `type: [..., "null"]` so
the LLM can explicitly report "not found" rather than hallucinating.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm.base import LLMError, LLMProvider, LLMResult
from app.modules.licitacoes.editais import list_anexos
from app.modules.licitacoes.models import Edital, EditalAnalise
from app.modules.licitacoes.pdf_extract import (
    ExtractionResult,
    extract_text_from_anexos,
)
from app.modules.licitacoes.storage import EditaisStorage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Voce e um analista de licitacoes publicas brasileiras
especializado em obras pesadas (terraplanagem, pavimentacao, drenagem,
construcao civil). Sua tarefa e extrair os campos estruturados do edital
fornecido. Use SOMENTE informacoes presentes no texto; se um campo nao
estiver explicitamente mencionado, retorne null. Nao invente numeros.
Percentuais devem ser retornados como numeros (ex: 5.0 para "5%")."""

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "prazo_execucao_dias": {
            "type": ["integer", "null"],
            "description": (
                "Prazo de execucao em dias corridos. Se o edital falar em "
                "meses, converter para dias (1 mes = 30)."
            ),
        },
        "garantia_percentual": {
            "type": ["number", "null"],
            "description": "Percentual da garantia contratual exigida (0-100).",
        },
        "bdi_maximo_percentual": {
            "type": ["number", "null"],
            "description": "BDI maximo imposto pelo edital (0-100), se houver.",
        },
        "atestados_cat": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "descricao": {"type": "string"},
                    "quantidade_minima": {"type": ["string", "null"]},
                },
                "required": ["descricao", "quantidade_minima"],
            },
            "description": (
                "CAT/atestados tecnicos exigidos. Cada item tem a descricao "
                "do servico e o volume minimo (string livre, ex: '5000 m3')."
            ),
        },
        "visita_tecnica_obrigatoria": {
            "type": ["boolean", "null"],
            "description": "True se visita tecnica for obrigatoria.",
        },
        "valor_estimado": {
            "type": ["number", "null"],
            "description": "Valor estimado / de referencia da contratacao (R$).",
        },
        "modalidade": {
            "type": ["string", "null"],
            "description": "Modalidade explicitada no edital (Pregao, RDC, Concorrencia, etc).",
        },
        "observacoes": {
            "type": ["string", "null"],
            "description": (
                "Resumo curto (<=400 chars) sobre riscos, condicoes "
                "especiais, ou pontos de atencao detectados."
            ),
        },
    },
    "required": [
        "prazo_execucao_dias",
        "garantia_percentual",
        "bdi_maximo_percentual",
        "atestados_cat",
        "visita_tecnica_obrigatoria",
        "valor_estimado",
        "modalidade",
        "observacoes",
    ],
}


MAX_PROMPT_CHARS = 400_000  # safety truncation before sending to LLM


async def get_analise(
    db: AsyncSession, edital_id: int
) -> EditalAnalise | None:
    stmt = select(EditalAnalise).where(EditalAnalise.edital_id == edital_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _get_or_create_analise(
    db: AsyncSession, *, edital_id: int
) -> EditalAnalise:
    existing = await get_analise(db, edital_id)
    if existing is not None:
        existing.status = "pending"
        existing.error_message = None
        await db.flush()
        return existing
    row = EditalAnalise(edital_id=edital_id, status="pending")
    db.add(row)
    await db.flush()
    return row


async def analyze_edital_for_licitacao(
    db: AsyncSession,
    *,
    licitacao_id: int,
    storage: EditaisStorage,
    llm: LLMProvider,
    max_prompt_chars: int = MAX_PROMPT_CHARS,
) -> EditalAnalise:
    """Run the LLM analysis for `licitacao_id` and persist the result.

    Raises:
        ValueError: when the licitacao has no downloaded edital yet.
    """
    edital = (
        await db.execute(
            select(Edital).where(Edital.licitacao_id == licitacao_id)
        )
    ).scalar_one_or_none()
    if edital is None:
        raise ValueError(
            f"licitacao {licitacao_id} sem edital baixado (rode D.4 primeiro)"
        )
    if edital.status not in {"completed", "empty"}:
        # We still accept "pending" re-analysis but empty+failed are no-ops.
        logger.info(
            "analyze_edital: edital %s em status %s", edital.id, edital.status
        )

    anexos = await list_anexos(db, edital.id)
    analise = await _get_or_create_analise(db, edital_id=edital.id)

    if not anexos:
        analise.status = "empty"
        analise.error_message = "edital sem anexos baixados"
        analise.anexos_analisados = 0
        analise.total_pages = 0
        await _commit_and_refresh(db, analise)
        return analise

    extraction = await extract_text_from_anexos(anexos, storage)
    prompt_text = extraction.combined_text[:max_prompt_chars]

    if not prompt_text.strip():
        analise.status = "empty"
        analise.error_message = "nenhum texto extraido dos anexos"
        analise.anexos_analisados = len(anexos)
        analise.total_pages = extraction.total_pages
        await _commit_and_refresh(db, analise)
        return analise

    try:
        result = await llm.analyze(
            text=prompt_text,
            schema=ANALYSIS_SCHEMA,
            system_prompt=SYSTEM_PROMPT,
        )
    except LLMError as exc:
        logger.warning("llm analise falhou p/ licitacao %s: %s", licitacao_id, exc)
        analise.status = "failed"
        analise.error_message = str(exc)[:1024]
        analise.anexos_analisados = len(anexos)
        analise.total_pages = extraction.total_pages
        await _commit_and_refresh(db, analise)
        return analise

    _apply_llm_result(analise, result, extraction, anexos_total=len(anexos))
    await _commit_and_refresh(db, analise)
    return analise


async def _commit_and_refresh(db: AsyncSession, row: EditalAnalise) -> None:
    """Commit and refresh so server-side defaults (`created_at`, `updated_at`)
    come back to Python. Without this, Pydantic model_validate triggers an
    async lazy-load from outside a greenlet context and blows up."""
    await db.commit()
    await db.refresh(row)


def _apply_llm_result(
    analise: EditalAnalise,
    result: LLMResult,
    extraction: ExtractionResult,
    *,
    anexos_total: int,
) -> None:
    data = result.data or {}
    analise.status = "completed"
    analise.provider = result.provider
    analise.model = result.model
    analise.data = data
    analise.anexos_analisados = anexos_total
    analise.total_pages = extraction.total_pages
    analise.prompt_tokens = result.prompt_tokens or None
    analise.completion_tokens = result.completion_tokens or None
    analise.cost_usd = (
        Decimal(str(result.cost_usd)) if result.cost_usd else None
    )
    analise.error_message = None

    analise.prazo_execucao_dias = _as_int(data.get("prazo_execucao_dias"))
    analise.garantia_percentual = _as_decimal(data.get("garantia_percentual"))
    analise.bdi_maximo_percentual = _as_decimal(data.get("bdi_maximo_percentual"))
    analise.visita_tecnica_obrigatoria = _as_bool(
        data.get("visita_tecnica_obrigatoria")
    )
    analise.valor_estimado = _as_decimal(data.get("valor_estimado"))


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "sim", "yes", "1"}:
            return True
        if normalized in {"false", "nao", "não", "no", "0"}:
            return False
    return None
