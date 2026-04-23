"""Service-level tests for the Licitacoes module (SQLite in-memory)."""
from __future__ import annotations

from datetime import date

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import PncpClient
from app.modules.licitacoes.service import ingest_publicacoes, list_licitacoes


def _payload_for(modalidade: int, page: int, paginas_restantes: int, n: int = 1) -> dict:
    return {
        "data": [
            {
                "numeroCompra": f"M{modalidade}-P{page}-{i}",
                "anoCompra": 2024,
                "sequencialCompra": modalidade * 100 + i,
                "objetoCompra": f"item {modalidade}-{page}-{i}",
                "modalidadeNome": f"Modalidade {modalidade}",
                "modoDisputaNome": "Aberto",
                "situacaoCompraNome": "Divulgada",
                "valorTotalEstimado": 100 + i,
                "valorTotalHomologado": None,
                "srp": False,
                "dataPublicacaoPncp": "2024-10-01T12:00:00",
                "orgaoEntidade": {
                    "cnpj": f"1234567800010{modalidade % 10}",
                    "razaoSocial": f"Orgao {modalidade}",
                    "poderId": "E",
                    "esferaId": "M",
                },
                "unidadeOrgao": {
                    "ufSigla": "SP",
                    "municipioNome": "Cidade X",
                    "codigoIbge": "3550308",
                    "nomeUnidade": "Unidade",
                    "codigoUnidade": "001",
                },
            }
            for i in range(n)
        ],
        "totalRegistros": n,
        "totalPaginas": 1,
        "numeroPagina": page,
        "paginasRestantes": paginas_restantes,
        "empty": n == 0,
    }


@pytest.mark.asyncio
async def test_ingest_upserts_idempotently(db_session: AsyncSession) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        modalidade = int(dict(request.url.params)["codigoModalidadeContratacao"])
        # every modalidade returns 1 row on page 1 then stops
        return httpx.Response(200, json=_payload_for(modalidade, page=1, paginas_restantes=0))

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://mock.test", transport=transport)
    client = PncpClient(base_url="https://mock.test", client=http)

    # Limit modalidades for test speed; 3 modalidades x 1 item = 3 rows.
    result = await ingest_publicacoes(
        db_session,
        client,
        data_inicial=date(2025, 4, 1),
        data_final=date(2025, 4, 2),
        modalidades=(6, 7, 8),
    )
    assert result.total_fetched == 3
    assert result.inserted == 3

    items, total = await list_licitacoes(db_session, uf="SP", page=1, page_size=50)
    assert total == 3
    assert len(items) == 3

    # Second run with same data must not duplicate rows.
    result2 = await ingest_publicacoes(
        db_session,
        client,
        data_inicial=date(2025, 4, 1),
        data_final=date(2025, 4, 2),
        modalidades=(6, 7, 8),
    )
    assert result2.total_fetched == 3
    _, total_after = await list_licitacoes(db_session, page=1, page_size=50)
    assert total_after == 3  # unchanged

    await client.aclose()


@pytest.mark.asyncio
async def test_ingest_skips_failed_modalidade_and_continues(
    db_session: AsyncSession,
) -> None:
    """One modalidade timing out must not abort the whole ingest."""

    def handler(request: httpx.Request) -> httpx.Response:
        modalidade = int(dict(request.url.params)["codigoModalidadeContratacao"])
        if modalidade == 7:
            raise httpx.ReadTimeout("simulated PNCP timeout", request=request)
        return httpx.Response(
            200, json=_payload_for(modalidade, page=1, paginas_restantes=0)
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://mock.test", transport=transport)
    client = PncpClient(base_url="https://mock.test", client=http)

    result = await ingest_publicacoes(
        db_session,
        client,
        data_inicial=date(2025, 4, 1),
        data_final=date(2025, 4, 2),
        modalidades=(6, 7, 8),
    )

    assert result.failed_modalidades == [7]
    assert result.total_fetched == 2  # 6 and 8 succeeded
    assert result.inserted == 2

    _, total = await list_licitacoes(db_session, page=1, page_size=50)
    assert total == 2

    await client.aclose()


@pytest.mark.asyncio
async def test_list_filters_by_uf(db_session: AsyncSession) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        modalidade = int(dict(request.url.params)["codigoModalidadeContratacao"])
        return httpx.Response(200, json=_payload_for(modalidade, page=1, paginas_restantes=0))

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://mock.test", transport=transport)
    client = PncpClient(base_url="https://mock.test", client=http)

    await ingest_publicacoes(
        db_session,
        client,
        data_inicial=date(2025, 4, 1),
        data_final=date(2025, 4, 2),
        modalidades=(6,),
    )

    items_sp, total_sp = await list_licitacoes(db_session, uf="SP")
    assert total_sp == 1
    items_rj, total_rj = await list_licitacoes(db_session, uf="RJ")
    assert total_rj == 0
    assert items_rj == []

    await client.aclose()
