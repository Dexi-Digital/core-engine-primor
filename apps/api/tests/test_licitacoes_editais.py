"""Tests for D.4 -- download de editais PNCP + armazenamento local."""
from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.comprasnet.client import (
    ComprasnetCaptchaRequired,
    extract_edital_links,
)
from app.integrations.licitacoes_e.client import (
    LicitacoesEClient,
    LicitacoesECredentialsRequired,
)
from app.integrations.pncp.client import (
    PncpArquivo,
    PncpClient,
    _extract_filename,
)
from app.modules.licitacoes.editais import download_edital_for_licitacao
from app.modules.licitacoes.models import AnexoEdital, Edital, Licitacao
from app.modules.licitacoes.storage import LocalStorage, _safe_filename

SAMPLE_ARQUIVOS = [
    {
        "uri": "https://pncp.gov.br/pncp-api/v1/orgaos/12345678000100/compras/2026/7/arquivos/1",
        "url": "https://pncp.gov.br/pncp-api/v1/orgaos/12345678000100/compras/2026/7/arquivos/1",
        "tipoDocumentoDescricao": "Edital",
        "statusAtivo": True,
        "cnpj": "12345678000100",
        "anoCompra": 2026,
        "sequencialCompra": 7,
        "dataPublicacaoPncp": "2026-04-22T11:45:01",
        "sequencialDocumento": 1,
        "titulo": "EDITAL_PREGAO_002",
        "tipoDocumentoId": 2,
    },
    {
        "uri": "https://pncp.gov.br/pncp-api/v1/orgaos/12345678000100/compras/2026/7/arquivos/2",
        "url": "https://pncp.gov.br/pncp-api/v1/orgaos/12345678000100/compras/2026/7/arquivos/2",
        "tipoDocumentoDescricao": "Outros Documentos",
        "statusAtivo": True,
        "cnpj": "12345678000100",
        "anoCompra": 2026,
        "sequencialCompra": 7,
        "dataPublicacaoPncp": "2026-04-22T11:45:03",
        "sequencialDocumento": 2,
        "titulo": "PROJETO_EXECUTIVO",
        "tipoDocumentoId": 16,
    },
]

PDF_BYTES_1 = b"%PDF-1.7\nsome binary body 1\n"
PDF_BYTES_2 = b"%PDF-1.7\nsome binary body 2\n" * 10


def _portal_transport(
    *, list_status: int = 200, arquivos: list[dict] | None = None
) -> httpx.MockTransport:
    """Build an httpx MockTransport matching the pncp-api portal endpoints."""
    body_list = SAMPLE_ARQUIVOS if arquivos is None else arquivos

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/arquivos"):
            if list_status == 204:
                return httpx.Response(204)
            if list_status == 404:
                return httpx.Response(404, text="not found")
            return httpx.Response(200, json=body_list)
        if request.url.path.endswith("/arquivos/1"):
            return httpx.Response(
                200,
                content=PDF_BYTES_1,
                headers={
                    "content-type": "application/octet-stream",
                    "content-disposition": 'attachment; filename="server_doc_1.pdf"',
                },
            )
        if request.url.path.endswith("/arquivos/2"):
            return httpx.Response(
                200,
                content=PDF_BYTES_2,
                headers={
                    "content-type": "application/octet-stream",
                    "content-disposition": 'attachment; filename="server_doc_2.pdf"',
                },
            )
        return httpx.Response(500, text="unexpected")

    return httpx.MockTransport(handler)


def _make_pncp_client(transport: httpx.MockTransport) -> PncpClient:
    # Inject a httpx.AsyncClient backed by MockTransport for the portal
    # endpoints. Consulta client is not used by D.4 so left default.
    portal_client = httpx.AsyncClient(
        transport=transport, base_url="https://pncp.gov.br/api/pncp"
    )
    return PncpClient(portal_client=portal_client)


async def _seed_licitacao(db: AsyncSession, **overrides) -> Licitacao:
    row = Licitacao(
        external_id=overrides.get("external_id", "12345678000100-2026-7"),
        source="pncp",
        numero_compra=overrides.get("numero_compra", "00007/2026"),
        ano_compra=overrides.get("ano_compra", 2026),
        sequencial_compra=overrides.get("sequencial_compra", 7),
        objeto_compra=overrides.get("objeto_compra", "Aquisicao de pavimentacao"),
        orgao_cnpj=overrides.get("orgao_cnpj", "12345678000100"),
        uf_sigla=overrides.get("uf_sigla", "SP"),
    )
    db.add(row)
    await db.flush()
    return row


# ---------- storage ----------


@pytest.mark.anyio
async def test_local_storage_writes_file_with_size():
    async def _stream() -> AsyncIterator[bytes]:
        for chunk in (b"abc", b"def", b""):
            yield chunk

    with tempfile.TemporaryDirectory() as tmp:
        storage = LocalStorage(tmp)
        path, size = await storage.save(
            licitacao_id=42, filename="edital.pdf", content=_stream()
        )
    # 'abc' + 'def' = 6 bytes; empty chunk skipped
    assert size == 6
    target = Path(path)
    assert target.name == "edital.pdf"
    assert target.parent.name == "42"


def test_safe_filename_strips_path_traversal():
    # Path separators get collapsed; leading dots stripped to block hidden files.
    result = _safe_filename("../../etc/passwd")
    assert "/" not in result and "\\" not in result
    assert not result.startswith(".")
    assert _safe_filename("normal.pdf") == "normal.pdf"
    assert _safe_filename("\x00weird") == "weird"
    assert _safe_filename("") == "anexo.bin"
    assert _safe_filename("...") == "anexo.bin"


# ---------- pncp client ----------


def test_extract_filename_handles_quoted_and_unquoted():
    h_quoted = httpx.Headers({"content-disposition": 'attachment; filename="xyz.pdf"'})
    h_plain = httpx.Headers({"content-disposition": "attachment; filename=xyz.pdf; size=10"})
    h_missing = httpx.Headers({})
    assert _extract_filename(h_quoted) == "xyz.pdf"
    assert _extract_filename(h_plain) == "xyz.pdf"
    assert _extract_filename(h_missing) is None


@pytest.mark.anyio
async def test_pncp_list_arquivos_happy_path():
    client = _make_pncp_client(_portal_transport())
    try:
        arquivos = await client.list_arquivos(
            cnpj="12345678000100", ano=2026, sequencial=7
        )
    finally:
        await client.aclose()
    assert len(arquivos) == 2
    assert all(isinstance(a, PncpArquivo) for a in arquivos)
    assert arquivos[0].tipo_documento_descricao == "Edital"
    assert arquivos[0].titulo == "EDITAL_PREGAO_002"


@pytest.mark.anyio
async def test_pncp_list_arquivos_returns_empty_on_204_and_404():
    for code in (204, 404):
        client = _make_pncp_client(_portal_transport(list_status=code))
        try:
            arquivos = await client.list_arquivos(
                cnpj="12345678000100", ano=2026, sequencial=7
            )
        finally:
            await client.aclose()
        assert arquivos == []


# ---------- service ----------


@pytest.mark.anyio
async def test_download_edital_creates_rows_and_files(db_session, tmp_path):
    licitacao = await _seed_licitacao(db_session)
    client = _make_pncp_client(_portal_transport())
    storage = LocalStorage(tmp_path)

    result = await download_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, pncp=client, storage=storage
    )
    await client.aclose()

    assert result.status == "completed"
    assert result.anexos_count == 2
    assert result.new_anexos == 2

    edital = (
        await db_session.execute(select(Edital).where(Edital.licitacao_id == licitacao.id))
    ).scalar_one()
    assert edital.source == "pncp"
    assert edital.anexos_count == 2

    anexos = (
        await db_session.execute(
            select(AnexoEdital).where(AnexoEdital.edital_id == edital.id)
        )
    ).scalars().all()
    assert {a.sequencial_documento for a in anexos} == {1, 2}

    # Files must exist on disk with the server-returned bytes.
    by_seq = {a.sequencial_documento: a for a in anexos}
    assert Path(by_seq[1].storage_path).read_bytes() == PDF_BYTES_1
    assert Path(by_seq[2].storage_path).read_bytes() == PDF_BYTES_2
    # Filename composition: titulo + server extension
    assert by_seq[1].filename == "EDITAL_PREGAO_002.pdf"
    assert by_seq[1].size_bytes == len(PDF_BYTES_1)


@pytest.mark.anyio
async def test_download_edital_is_idempotent(db_session, tmp_path):
    licitacao = await _seed_licitacao(db_session)
    storage = LocalStorage(tmp_path)

    client = _make_pncp_client(_portal_transport())
    first = await download_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, pncp=client, storage=storage
    )
    await client.aclose()

    client2 = _make_pncp_client(_portal_transport())
    second = await download_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, pncp=client2, storage=storage
    )
    await client2.aclose()

    assert first.new_anexos == 2
    assert second.new_anexos == 0
    assert second.anexos_count == 2

    # No duplicate anexos -- idempotent re-run must leave the table at 2 rows.
    anexos = (
        await db_session.execute(select(AnexoEdital))
    ).scalars().all()
    assert len(anexos) == 2


@pytest.mark.anyio
async def test_download_edital_empty_when_pncp_has_no_files(db_session, tmp_path):
    licitacao = await _seed_licitacao(db_session)
    client = _make_pncp_client(_portal_transport(arquivos=[]))
    storage = LocalStorage(tmp_path)

    result = await download_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, pncp=client, storage=storage
    )
    await client.aclose()

    assert result.status == "empty"
    assert result.anexos_count == 0
    edital = (
        await db_session.execute(select(Edital))
    ).scalar_one()
    assert edital.status == "empty"


@pytest.mark.anyio
async def test_download_edital_rejects_licitacao_without_triplet(db_session, tmp_path):
    licitacao = Licitacao(
        external_id="missing-triplet",
        source="pncp",
        orgao_cnpj=None,
        ano_compra=None,
        sequencial_compra=None,
    )
    db_session.add(licitacao)
    await db_session.flush()

    client = _make_pncp_client(_portal_transport())
    storage = LocalStorage(tmp_path)
    with pytest.raises(ValueError):
        await download_edital_for_licitacao(
            db_session, licitacao_id=licitacao.id, pncp=client, storage=storage
        )
    await client.aclose()


# ---------- router ----------


@pytest.mark.anyio
async def test_get_edital_404_before_download(api_client, db_session):
    licitacao = await _seed_licitacao(db_session)
    await db_session.commit()
    resp = await api_client.get(f"/api/v1/licitacoes/{licitacao.id}/edital")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_post_download_and_get_edital_roundtrip(
    api_client, db_session, tmp_path, monkeypatch
):
    from app.main import app
    from app.modules.licitacoes.router import (
        get_editais_storage,
        get_pncp_client,
    )

    licitacao = await _seed_licitacao(db_session)
    await db_session.commit()

    app.dependency_overrides[get_pncp_client] = lambda: _make_pncp_client(_portal_transport())
    app.dependency_overrides[get_editais_storage] = lambda: LocalStorage(tmp_path)
    try:
        post = await api_client.post(
            f"/api/v1/licitacoes/{licitacao.id}/edital/download"
        )
        assert post.status_code == 200, post.text
        body = post.json()
        assert body["status"] == "completed"
        assert body["anexos_count"] == 2

        get = await api_client.get(f"/api/v1/licitacoes/{licitacao.id}/edital")
        assert get.status_code == 200, get.text
        payload = get.json()
        assert payload["anexos_count"] == 2
        assert len(payload["anexos"]) == 2
        assert payload["anexos"][0]["filename"].endswith(".pdf")
    finally:
        app.dependency_overrides.pop(get_pncp_client, None)
        app.dependency_overrides.pop(get_editais_storage, None)


# ---------- fallback scaffolds ----------


def test_comprasnet_extract_edital_links_parses_relative_hrefs():
    html = (
        '<html><body>'
        '<a href="download_editais_detalhe.asp?coduasg=1&modprp=5&numprp=900001">x</a>'
        '<a href="https://example.com/other">y</a>'
        '<a HREF="download_editais_detalhe.asp?pagina=2">z</a>'
        "</body></html>"
    )
    links = extract_edital_links(html)
    assert any("numprp=900001" in link for link in links)
    assert any("pagina=2" in link for link in links)
    assert len(links) == 2


@pytest.mark.anyio
async def test_comprasnet_download_raises_captcha_required():
    from app.integrations.comprasnet.client import ComprasnetClient

    client = ComprasnetClient()
    try:
        with pytest.raises(ComprasnetCaptchaRequired):
            await client.download_edital_pdf(uasg="1", modalidade="5", numero_prp="1")
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_licitacoes_e_download_raises_credentials_required():
    client = LicitacoesEClient()
    with pytest.raises(LicitacoesECredentialsRequired):
        await client.download_edital_pdf(edital_id="x")
    assert await client.health_check() is False


# ---------- regressions: reviewed bugs fixed after merge ----------


@pytest.mark.anyio
async def test_stream_arquivo_closes_response_on_http_error():
    """Regression: raise_for_status() in stream_arquivo used to leak the
    streamed response when the server returned a non-2xx status."""
    closed: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)

    # Wrap transport so we can observe aclose() on the response.
    class _Recorder(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):  # type: ignore[override]
            resp = await transport.handle_async_request(request)
            original_aclose = resp.aclose

            async def _track():
                closed.append(True)
                await original_aclose()

            resp.aclose = _track  # type: ignore[method-assign]
            return resp

    portal = httpx.AsyncClient(
        transport=_Recorder(), base_url="https://pncp.gov.br/api/pncp"
    )
    client = PncpClient(portal_client=portal)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.stream_arquivo("https://pncp.gov.br/api/pncp/whatever")
    finally:
        await client.aclose()

    assert closed, "stream_arquivo must aclose() the response on HTTP error"


@pytest.mark.anyio
async def test_download_edital_skips_arquivo_with_empty_url(db_session, tmp_path):
    """Regression: a PncpArquivo with empty url raised ValueError, aborting
    the batch and losing progress on previously successful files."""
    arquivos_with_empty = [
        {**SAMPLE_ARQUIVOS[0], "url": "", "uri": ""},  # empty url -> ValueError
        SAMPLE_ARQUIVOS[1],                             # valid -> must still be saved
    ]

    licitacao = await _seed_licitacao(db_session)
    client = _make_pncp_client(_portal_transport(arquivos=arquivos_with_empty))
    storage = LocalStorage(tmp_path)

    result = await download_edital_for_licitacao(
        db_session, licitacao_id=licitacao.id, pncp=client, storage=storage
    )
    await client.aclose()

    # Bad arquivo skipped, good one persisted + committed.
    assert result.status == "completed"
    assert result.new_anexos == 1
    assert result.anexos_count == 1

    anexos = (await db_session.execute(select(AnexoEdital))).scalars().all()
    assert {a.sequencial_documento for a in anexos} == {2}
