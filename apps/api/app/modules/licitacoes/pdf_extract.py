"""Turn a list of downloaded anexos into a single text blob for LLM input.

D.5 feeds concatenated edital+termos+projetos to the LLM in one call.
Constraints:

* **Size cap** per file -- ignore giant binaries (planilhas, fotos rasterizadas,
  projetos DWG convertidos que somam 100+ MB). They blow up prompts
  and almost never contain the structured fields we want (prazo, garantia,
  BDI). The cap is configurable via settings.
* **Text-only** -- if a file is not a PDF (images, zips, dwg), we skip it.
* **Per-page truncation** -- we cap the total characters extracted per
  anexo to `max_chars_per_anexo`. This guards against adversarial PDFs
  that contain millions of whitespace characters.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.modules.licitacoes.models import AnexoEdital
from app.modules.licitacoes.storage import EditaisStorage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtractedAnexo:
    anexo_id: int
    filename: str
    num_pages: int
    text: str
    skipped_reason: str | None = None


@dataclass(slots=True)
class ExtractionResult:
    anexos: list[ExtractedAnexo]
    combined_text: str
    total_pages: int

    @property
    def is_empty(self) -> bool:
        return not self.combined_text.strip()


async def extract_text_from_anexos(
    anexos: list[AnexoEdital],
    storage: EditaisStorage,
    *,
    max_bytes_per_anexo: int = 25 * 1024 * 1024,  # 25 MiB
    max_chars_per_anexo: int = 200_000,
) -> ExtractionResult:
    """Extract raw text from every PDF anexo. Non-PDFs are skipped."""
    extracted: list[ExtractedAnexo] = []
    buf: list[str] = []
    total_pages = 0

    for anexo in anexos:
        filename = anexo.filename or "anexo.pdf"
        if not _looks_like_pdf(filename, anexo.content_type):
            extracted.append(
                ExtractedAnexo(
                    anexo_id=anexo.id,
                    filename=filename,
                    num_pages=0,
                    text="",
                    skipped_reason="not-pdf",
                )
            )
            continue

        if anexo.size_bytes is not None and anexo.size_bytes > max_bytes_per_anexo:
            extracted.append(
                ExtractedAnexo(
                    anexo_id=anexo.id,
                    filename=filename,
                    num_pages=0,
                    text="",
                    skipped_reason=f"too-large:{anexo.size_bytes}b",
                )
            )
            continue

        try:
            data = await storage.read(anexo.storage_path)
        except (FileNotFoundError, OSError) as exc:
            logger.warning(
                "storage read failed for anexo %s: %s", anexo.id, exc
            )
            extracted.append(
                ExtractedAnexo(
                    anexo_id=anexo.id,
                    filename=filename,
                    num_pages=0,
                    text="",
                    skipped_reason=f"io-error:{exc.__class__.__name__}",
                )
            )
            continue

        text, pages = _extract_pdf_text(data, max_chars=max_chars_per_anexo)
        extracted.append(
            ExtractedAnexo(
                anexo_id=anexo.id,
                filename=filename,
                num_pages=pages,
                text=text,
                skipped_reason=None if text else "empty-text",
            )
        )
        total_pages += pages
        if text:
            buf.append(f"# {filename}\n{text}")

    combined = "\n\n".join(buf)
    return ExtractionResult(
        anexos=extracted, combined_text=combined, total_pages=total_pages
    )


def _looks_like_pdf(filename: str, content_type: str | None) -> bool:
    if content_type and "pdf" in content_type.lower():
        return True
    return filename.lower().endswith(".pdf")


def _extract_pdf_text(data: bytes, *, max_chars: int) -> tuple[str, int]:
    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, Exception) as exc:  # pypdf raises many subclasses
        logger.warning("pypdf failed to open: %s", exc)
        return "", 0

    pages_text: list[str] = []
    total = 0
    num_pages = len(reader.pages)
    for page in reader.pages:
        try:
            snippet = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - pypdf raises unruly errors
            logger.warning("pypdf extract_text failed: %s", exc)
            snippet = ""
        if not snippet:
            continue
        pages_text.append(snippet)
        total += len(snippet)
        if total >= max_chars:
            break
    joined = "\n".join(pages_text)
    return joined[:max_chars], num_pages
