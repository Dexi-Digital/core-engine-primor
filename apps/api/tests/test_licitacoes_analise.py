"""Tests for D.5 -- analise LLM de editais."""
from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.integrations.llm.anthropic_client import AnthropicProvider
from app.integrations.llm.base import (
    LLMError,
    LLMResult,
    LLMUnavailableError,
)
from app.integrations.llm.openai_client import OpenAIProvider
from app.integrations.llm.router import CostRoutedProvider
from app.modules.licitacoes.analise import (
    ANALYSIS_SCHEMA,
    SYSTEM_PROMPT,
    analyze_edital_for_licitacao,
)
from app.modules.licitacoes.models import (
    AnexoEdital,
    Edital,
    EditalAnalise,
    Licitacao,
)
from app.modules.licitacoes.pdf_extract import extract_text_from_anexos
from app.modules.licitacoes.storage import LocalStorage

# ---------- helpers ----------


def _make_minimal_pdf_with_text(text: str) -> bytes:
    """Return a minimal PDF byte-string whose extracted text == `text`.

    pypdf cannot write a PDF with real text layers (that requires a full
    PDF generator like reportlab). Instead we build a hand-crafted PDF
    using low-level primitives that pypdf can parse and read back.
    """
    # Escape parentheses and backslashes as the PDF text operator requires.
    escaped = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
    # Minimal 1-page PDF with one Helvetica text showing (text).
    body = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length "
        + str(len(escaped) + 40).encode()
        + b">>stream\nBT /F1 12 Tf 50 700 Td ("
        + escaped.encode("latin-1", errors="replace")
        + b") Tj ET\nendstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"xref\n0 6\n0000000000 65535 f\n"
        b"0000000009 00000 n\n0000000052 00000 n\n"
        b"0000000101 00000 n\n0000000199 00000 n\n"
        b"0000000299 00000 n\n"
        b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n350\n%%EOF\n"
    )
    return body


def _make_blank_pdf(pages: int = 1) -> bytes:
    """Fallback used only for structural tests -- produces an empty PDF."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


async def _seed_edital_with_pdfs(
    db_session,
    storage: LocalStorage,
    *,
    licitacao_id: int | None = None,
    pdfs: list[tuple[str, bytes]] | None = None,
    content_types: list[str | None] | None = None,
) -> tuple[Licitacao, Edital, list[AnexoEdital]]:
    """Create licitacao + edital + anexos with real files written to disk."""
    licitacao = Licitacao(
        external_id=f"12345678000100-2026-{licitacao_id or 7}",
        source="pncp",
        orgao_cnpj="12345678000100",
        ano_compra=2026,
        sequencial_compra=licitacao_id or 7,
        objeto_compra="Pavimentacao asfaltica",
        uf_sigla="SP",
    )
    db_session.add(licitacao)
    await db_session.flush()

    edital = Edital(
        licitacao_id=licitacao.id, source="pncp", status="completed"
    )
    db_session.add(edital)
    await db_session.flush()

    anexos: list[AnexoEdital] = []
    pdfs = pdfs or []
    ctypes = content_types or [None] * len(pdfs)
    for seq, ((filename, data), ctype) in enumerate(zip(pdfs, ctypes, strict=False), start=1):
        subdir = storage.root / str(licitacao.id)
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / filename
        path.write_bytes(data)
        anexo = AnexoEdital(
            edital_id=edital.id,
            sequencial_documento=seq,
            titulo=filename.rsplit(".", 1)[0],
            source_url=f"https://pncp/fake/{seq}",
            filename=filename,
            storage_path=str(path),
            size_bytes=len(data),
            content_type=ctype,
        )
        db_session.add(anexo)
        anexos.append(anexo)
    await db_session.flush()
    edital.anexos_count = len(anexos)
    await db_session.flush()
    return licitacao, edital, anexos


class FakeLLMProvider:
    """In-memory LLM provider used by the service tests.

    Records the last prompt it saw so tests can assert what was sent.
    """

    name = "fake"
    model = "fake-model"
    input_price_per_mtok = 0.0

    def __init__(self, response: dict[str, Any] | None = None, *, raise_error: Exception | None = None):
        self._response = response or {
            "prazo_execucao_dias": 180,
            "garantia_percentual": 5.0,
            "bdi_maximo_percentual": 25.5,
            "atestados_cat": [
                {"descricao": "pavimentacao asfaltica", "quantidade_minima": "10000 m2"}
            ],
            "visita_tecnica_obrigatoria": True,
            "valor_estimado": 1250000.00,
            "modalidade": "Concorrencia",
            "observacoes": "Obra com prazo apertado, BDI limitado.",
        }
        self._raise = raise_error
        self.calls: list[dict[str, Any]] = []

    async def analyze(self, *, text: str, schema: dict, system_prompt: str) -> LLMResult:
        self.calls.append({"text": text, "schema": schema, "system_prompt": system_prompt})
        if self._raise is not None:
            raise self._raise
        return LLMResult(
            data=self._response,
            provider=self.name,
            model=self.model,
            prompt_tokens=1200,
            completion_tokens=200,
            cost_usd=0.00015,
        )

    async def aclose(self) -> None:
        return None


# ---------- pdf_extract ----------


@pytest.mark.anyio
async def test_extract_text_reads_real_pdf_from_storage(db_session, tmp_path):
    storage = LocalStorage(tmp_path)
    pdf_bytes = _make_minimal_pdf_with_text("prazo de execucao 180 dias")
    _lic, _ed, anexos = await _seed_edital_with_pdfs(
        db_session, storage, pdfs=[("edital.pdf", pdf_bytes)]
    )
    result = await extract_text_from_anexos(anexos, storage)
    assert result.total_pages == 1
    assert "prazo" in result.combined_text.lower()
    assert result.anexos[0].skipped_reason is None


@pytest.mark.anyio
async def test_extract_text_skips_non_pdf_and_oversized(db_session, tmp_path):
    storage = LocalStorage(tmp_path)
    # 1 normal PDF, 1 non-PDF (zip), 1 huge PDF past the cap
    normal = _make_minimal_pdf_with_text("texto curto")
    huge = _make_blank_pdf(pages=1) + b"\x00" * 100  # size_bytes forced below

    lic = Licitacao(
        external_id="skip-test", source="pncp", orgao_cnpj="x", ano_compra=2026,
        sequencial_compra=1,
    )
    db_session.add(lic)
    await db_session.flush()
    edital = Edital(licitacao_id=lic.id, source="pncp", status="completed")
    db_session.add(edital)
    await db_session.flush()

    subdir = Path(tmp_path) / str(lic.id)
    subdir.mkdir()
    (subdir / "edital.pdf").write_bytes(normal)
    (subdir / "projeto.zip").write_bytes(b"PK\x03\x04fake zip")
    (subdir / "huge.pdf").write_bytes(huge)

    anexos = [
        AnexoEdital(
            edital_id=edital.id, sequencial_documento=1, titulo="a",
            source_url="x", filename="edital.pdf",
            storage_path=str(subdir / "edital.pdf"),
            size_bytes=len(normal), content_type="application/pdf",
        ),
        AnexoEdital(
            edital_id=edital.id, sequencial_documento=2, titulo="b",
            source_url="x", filename="projeto.zip",
            storage_path=str(subdir / "projeto.zip"),
            size_bytes=12, content_type="application/zip",
        ),
        AnexoEdital(
            edital_id=edital.id, sequencial_documento=3, titulo="c",
            source_url="x", filename="huge.pdf",
            storage_path=str(subdir / "huge.pdf"),
            size_bytes=999_999_999, content_type="application/pdf",
        ),
    ]
    for a in anexos:
        db_session.add(a)
    await db_session.flush()

    result = await extract_text_from_anexos(anexos, storage)
    by_name = {x.filename: x for x in result.anexos}
    assert by_name["edital.pdf"].skipped_reason is None
    assert by_name["projeto.zip"].skipped_reason == "not-pdf"
    assert by_name["huge.pdf"].skipped_reason and by_name["huge.pdf"].skipped_reason.startswith("too-large")


# ---------- analise service ----------


@pytest.mark.anyio
async def test_analyze_edital_persists_structured_result(db_session, tmp_path):
    storage = LocalStorage(tmp_path)
    pdf_bytes = _make_minimal_pdf_with_text(
        "prazo de execucao: 180 dias. garantia 5%. BDI maximo 25,5%."
    )
    licitacao, edital, _ = await _seed_edital_with_pdfs(
        db_session, storage, pdfs=[("edital.pdf", pdf_bytes)]
    )

    llm = FakeLLMProvider()
    analise = await analyze_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, storage=storage, llm=llm
    )

    assert analise.status == "completed"
    assert analise.provider == "fake"
    assert analise.prazo_execucao_dias == 180
    assert analise.garantia_percentual == Decimal("5.0")
    assert analise.bdi_maximo_percentual == Decimal("25.5")
    assert analise.visita_tecnica_obrigatoria is True
    assert analise.valor_estimado == Decimal("1250000.00")
    assert analise.data is not None
    assert analise.data["atestados_cat"][0]["descricao"] == "pavimentacao asfaltica"
    assert analise.cost_usd == Decimal("0.00015")
    assert analise.prompt_tokens == 1200
    assert analise.anexos_analisados == 1
    assert analise.total_pages == 1

    # LLM was called with the expected schema + system prompt
    assert len(llm.calls) == 1
    assert llm.calls[0]["schema"] is ANALYSIS_SCHEMA
    assert llm.calls[0]["system_prompt"] == SYSTEM_PROMPT


@pytest.mark.anyio
async def test_analyze_edital_is_idempotent_updates_single_row(db_session, tmp_path):
    storage = LocalStorage(tmp_path)
    pdf_bytes = _make_minimal_pdf_with_text("licitacao pavimentacao asfaltica 2026")
    licitacao, _, _ = await _seed_edital_with_pdfs(
        db_session, storage, pdfs=[("edital.pdf", pdf_bytes)]
    )

    llm = FakeLLMProvider()
    first = await analyze_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, storage=storage, llm=llm
    )
    second = await analyze_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, storage=storage, llm=llm
    )

    assert first.id == second.id
    rows = (await db_session.execute(select(EditalAnalise))).scalars().all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_analyze_edital_raises_when_no_edital(db_session, tmp_path):
    storage = LocalStorage(tmp_path)
    lic = Licitacao(
        external_id="no-edital", source="pncp",
        orgao_cnpj="x", ano_compra=2026, sequencial_compra=1,
    )
    db_session.add(lic)
    await db_session.flush()

    llm = FakeLLMProvider()
    with pytest.raises(ValueError):
        await analyze_edital_for_licitacao(
            db_session, licitacao_id=lic.id, storage=storage, llm=llm
        )


@pytest.mark.anyio
async def test_analyze_edital_handles_llm_error(db_session, tmp_path):
    storage = LocalStorage(tmp_path)
    pdf_bytes = _make_minimal_pdf_with_text("texto")
    licitacao, _, _ = await _seed_edital_with_pdfs(
        db_session, storage, pdfs=[("edital.pdf", pdf_bytes)]
    )
    llm = FakeLLMProvider(raise_error=LLMError("rate limited"))

    analise = await analyze_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, storage=storage, llm=llm
    )
    assert analise.status == "failed"
    assert "rate limited" in (analise.error_message or "")


@pytest.mark.anyio
async def test_analyze_edital_empty_when_no_anexos(db_session, tmp_path):
    storage = LocalStorage(tmp_path)
    licitacao, edital, _ = await _seed_edital_with_pdfs(
        db_session, storage, pdfs=[]
    )
    edital.status = "empty"
    await db_session.flush()

    llm = FakeLLMProvider()
    analise = await analyze_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, storage=storage, llm=llm
    )
    assert analise.status == "empty"
    # LLM must NOT be called when there is nothing to extract
    assert llm.calls == []


# ---------- router ----------


@pytest.mark.anyio
async def test_get_analise_404_before_post(api_client, db_session, tmp_path):
    storage = LocalStorage(tmp_path)
    pdf_bytes = _make_minimal_pdf_with_text("x")
    licitacao, _, _ = await _seed_edital_with_pdfs(
        db_session, storage, pdfs=[("edital.pdf", pdf_bytes)]
    )
    await db_session.commit()

    resp = await api_client.get(f"/api/v1/licitacoes/{licitacao.id}/edital/analise")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_post_analise_503_when_llm_unconfigured(
    api_client, db_session, tmp_path, monkeypatch
):
    """No ANTHROPIC_API_KEY / OPENAI_API_KEY -> service returns 503."""
    from app.core.config import get_settings
    from app.main import app
    from app.modules.licitacoes.router import get_editais_storage

    storage = LocalStorage(tmp_path)
    pdf_bytes = _make_minimal_pdf_with_text("x")
    licitacao, _, _ = await _seed_edital_with_pdfs(
        db_session, storage, pdfs=[("edital.pdf", pdf_bytes)]
    )
    await db_session.commit()

    app.dependency_overrides[get_editais_storage] = lambda: storage
    # ensure both keys are unset
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", None)
    try:
        resp = await api_client.post(
            f"/api/v1/licitacoes/{licitacao.id}/edital/analise"
        )
        assert resp.status_code == 503
        assert "ANTHROPIC_API_KEY" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_editais_storage, None)


@pytest.mark.anyio
async def test_post_then_get_analise_roundtrip(
    api_client, db_session, tmp_path
):
    """End-to-end HTTP: POST runs the analysis, GET returns the persisted row."""
    from app.main import app
    from app.modules.licitacoes.router import (
        get_editais_storage,
        get_llm_provider,
    )

    storage = LocalStorage(tmp_path)
    pdf_bytes = _make_minimal_pdf_with_text("prazo 120 dias garantia 3% BDI 20%")
    licitacao, _, _ = await _seed_edital_with_pdfs(
        db_session, storage, pdfs=[("edital.pdf", pdf_bytes)]
    )
    await db_session.commit()

    fake = FakeLLMProvider(response={
        "prazo_execucao_dias": 120,
        "garantia_percentual": 3.0,
        "bdi_maximo_percentual": 20.0,
        "atestados_cat": [],
        "visita_tecnica_obrigatoria": False,
        "valor_estimado": None,
        "modalidade": "Pregao",
        "observacoes": "ok",
    })
    app.dependency_overrides[get_editais_storage] = lambda: storage
    app.dependency_overrides[get_llm_provider] = lambda: fake
    try:
        post = await api_client.post(
            f"/api/v1/licitacoes/{licitacao.id}/edital/analise"
        )
        assert post.status_code == 200, post.text
        body = post.json()
        assert body["status"] == "completed"
        assert body["prazo_execucao_dias"] == 120
        assert body["provider"] == "fake"

        get = await api_client.get(
            f"/api/v1/licitacoes/{licitacao.id}/edital/analise"
        )
        assert get.status_code == 200
        payload = get.json()
        assert payload["data"]["modalidade"] == "Pregao"
    finally:
        app.dependency_overrides.pop(get_editais_storage, None)
        app.dependency_overrides.pop(get_llm_provider, None)


# ---------- LLM providers (wire-level, mocked) ----------


@pytest.mark.anyio
async def test_anthropic_provider_builds_tool_use_request():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "sk-test"
        body = request.read()
        assert b'"tool_choice"' in body
        assert b'"extract_edital"' in body
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-3-5-haiku-20241022",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "extract_edital",
                        "input": {"prazo_execucao_dias": 90},
                    }
                ],
                "usage": {"input_tokens": 800, "output_tokens": 50},
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        transport=transport,
        base_url="https://api.anthropic.com",
        headers={"x-api-key": "sk-test", "anthropic-version": "2023-06-01"},
    )
    provider = AnthropicProvider(
        api_key="sk-test",
        http_client=http,
    )
    result = await provider.analyze(
        text="edital content",
        schema=ANALYSIS_SCHEMA,
        system_prompt=SYSTEM_PROMPT,
    )
    await provider.aclose()

    assert result.data == {"prazo_execucao_dias": 90}
    assert result.prompt_tokens == 800
    assert result.completion_tokens == 50
    assert result.cost_usd > 0
    assert result.provider == "anthropic"


@pytest.mark.anyio
async def test_anthropic_provider_raises_llm_error_on_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server down")

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.anthropic.com",
    )
    provider = AnthropicProvider(api_key="sk-test", http_client=http)
    with pytest.raises(LLMError):
        await provider.analyze(
            text="x", schema=ANALYSIS_SCHEMA, system_prompt=SYSTEM_PROMPT
        )
    await provider.aclose()


def test_anthropic_unavailable_without_key():
    with pytest.raises(LLMUnavailableError):
        AnthropicProvider(api_key="")


@pytest.mark.anyio
async def test_openai_provider_parses_json_schema_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = request.read()
        assert b'"response_format"' in body
        assert b'"json_schema"' in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"prazo_execucao_dias": 150}',
                            "role": "assistant",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 600, "completion_tokens": 20},
            },
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.openai.com"
    )
    provider = OpenAIProvider(api_key="sk-test", http_client=http)
    result = await provider.analyze(
        text="x", schema=ANALYSIS_SCHEMA, system_prompt=SYSTEM_PROMPT
    )
    await provider.aclose()

    assert result.data == {"prazo_execucao_dias": 150}
    assert result.provider == "openai"
    assert result.prompt_tokens == 600


@pytest.mark.anyio
async def test_openai_provider_raises_on_invalid_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "not json", "role": "assistant"}}],
                "usage": {},
            },
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.openai.com"
    )
    provider = OpenAIProvider(api_key="sk-test", http_client=http)
    with pytest.raises(LLMError):
        await provider.analyze(
            text="x", schema=ANALYSIS_SCHEMA, system_prompt=SYSTEM_PROMPT
        )
    await provider.aclose()


# ---------- cost router ----------


class _StaticProvider:
    """Tiny stub to test router ordering without hitting httpx."""

    def __init__(self, name: str, price: float, *, raise_error: Exception | None = None, data=None):
        self.name = name
        self.model = f"{name}-model"
        self.input_price_per_mtok = price
        self._raise = raise_error
        self._data = data or {"ok": True}
        self.called = False

    async def analyze(self, *, text, schema, system_prompt) -> LLMResult:
        self.called = True
        if self._raise:
            raise self._raise
        return LLMResult(
            data=self._data, provider=self.name, model=self.model,
            prompt_tokens=10, completion_tokens=5, cost_usd=0.0,
        )

    async def aclose(self) -> None:
        return None


@pytest.mark.anyio
async def test_cost_router_picks_cheapest_first():
    cheap = _StaticProvider("cheap", price=0.1, data={"from": "cheap"})
    expensive = _StaticProvider("expensive", price=5.0, data={"from": "expensive"})
    router = CostRoutedProvider([expensive, cheap])  # out-of-order input
    result = await router.analyze(
        text="x", schema=ANALYSIS_SCHEMA, system_prompt=SYSTEM_PROMPT
    )
    await router.aclose()
    assert result.data == {"from": "cheap"}
    assert cheap.called is True
    assert expensive.called is False


@pytest.mark.anyio
async def test_cost_router_falls_back_on_llm_error():
    cheap = _StaticProvider("cheap", price=0.1, raise_error=LLMError("rate limit"))
    backup = _StaticProvider("backup", price=1.0, data={"from": "backup"})
    router = CostRoutedProvider([cheap, backup])
    result = await router.analyze(
        text="x", schema=ANALYSIS_SCHEMA, system_prompt=SYSTEM_PROMPT
    )
    await router.aclose()
    assert result.data == {"from": "backup"}


@pytest.mark.anyio
async def test_cost_router_raises_when_all_providers_fail():
    a = _StaticProvider("a", price=0.1, raise_error=LLMError("a down"))
    b = _StaticProvider("b", price=1.0, raise_error=LLMError("b down"))
    router = CostRoutedProvider([a, b])
    with pytest.raises(LLMError):
        await router.analyze(
            text="x", schema=ANALYSIS_SCHEMA, system_prompt=SYSTEM_PROMPT
        )
    await router.aclose()


def test_cost_router_raises_when_no_providers():
    with pytest.raises(LLMUnavailableError):
        CostRoutedProvider([])
