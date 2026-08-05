"""Squad 3: ingestao de atas de registro de preco (D.9)."""
from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pncp.client import PncpClient
from app.modules.licitacoes.atas import external_id_from_numero_controle, ingest_atas
from app.modules.licitacoes.models import AtaRegistroPreco, Licitacao


def test_external_id_from_numero_controle() -> None:
    assert (
        external_id_from_numero_controle("00394460000141-1-000156/2024")
        == "00394460000141-2024-156"
    )
    assert external_id_from_numero_controle(None) is None
    assert external_id_from_numero_controle("garbage") is None


def _ata_payload(numero_controle_ata: str, cancelado: bool = False) -> dict:
    return {
        "numeroControlePNCPAta": numero_controle_ata,
        "numeroAtaRegistroPreco": "001/2026",
        "anoAta": 2026,
        "numeroControlePNCPCompra": "00394460000141-1-000156/2024",
        "cancelado": cancelado,
        "vigenciaInicio": "2026-01-01",
        "vigenciaFim": "2026-12-31",
        "objetoContratacao": "RP pavimentacao",
        "cnpjOrgao": "00394460000141",
        "nomeOrgao": "Prefeitura X",
        "possibilidadeAdesao": True,
    }


def _client_returning(atas: list[dict]) -> PncpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": atas,
                "totalRegistros": len(atas),
                "totalPaginas": 1,
                "numeroPagina": 1,
                "paginasRestantes": 0,
                "empty": not atas,
            },
        )

    http = httpx.AsyncClient(base_url="https://mock.test", transport=httpx.MockTransport(handler))
    return PncpClient(base_url="https://mock.test", client=http)


@pytest.mark.asyncio
async def test_ingest_atas_upsert_e_vinculo(db_session: AsyncSession) -> None:
    lic = Licitacao(
        external_id="00394460000141-2024-156",
        source="pncp",
        ano_compra=2024,
        sequencial_compra=156,
        orgao_cnpj="00394460000141",
        uf_sigla="MG",
        data_publicacao_pncp=datetime(2026, 7, 1, tzinfo=UTC),
    )
    db_session.add(lic)
    await db_session.commit()

    client = _client_returning([_ata_payload("ata-001")])
    s1 = await ingest_atas(
        db_session, client, data_inicial=date(2026, 1, 1), data_final=date(2026, 3, 1)
    )
    assert s1.total_fetched == 1
    assert s1.gravadas == 1
    assert s1.vinculadas == 1

    ata = (await db_session.execute(select(AtaRegistroPreco))).scalar_one()
    assert ata.licitacao_id == lic.id
    assert ata.vigencia_fim == date(2026, 12, 31)

    # re-ingestao com cancelamento: atualiza a MESMA row
    client2 = _client_returning([_ata_payload("ata-001", cancelado=True)])
    s2 = await ingest_atas(
        db_session, client2, data_inicial=date(2026, 1, 1), data_final=date(2026, 3, 1)
    )
    assert s2.gravadas == 0
    assert s2.atualizadas == 1
    rows = (await db_session.execute(select(AtaRegistroPreco))).scalars().all()
    assert len(rows) == 1
    assert rows[0].cancelado is True
    await client.aclose()
    await client2.aclose()


@pytest.mark.asyncio
async def test_ingest_atas_endpoint_requires_auth(api_client) -> None:
    resp = await api_client.post("/api/v1/licitacoes/ingest/atas")
    assert resp.status_code == 401
