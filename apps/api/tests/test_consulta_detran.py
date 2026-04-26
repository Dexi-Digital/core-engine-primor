"""Testes de consulta Detran (Modulo B.3) -- service + router."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.main import app
from app.modules.manutencao_frota import service
from app.modules.manutencao_frota.models import (
    ConsultaDetran,
    DocumentoVeiculo,
)
from app.modules.manutencao_frota.router import get_infosimples_dep

# --- fakes -----------------------------------------------------------------


class FakeOkClient:
    """Devolve um payload normalizado fixo, igualzinho ao real."""

    is_mock = False
    name = "infosimples"

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._payload = payload

    async def consultar_veiculo(self, placa: str, uf: str) -> dict[str, Any]:
        self.calls.append((placa, uf))
        return self._payload or {
            "uf": uf,
            "placa": placa,
            "renavam": "12345678900",
            "chassi": "9BW1234ABC56DEFGH",
            "marca_modelo": "VW/CONSTELLATION",
            "ano_modelo": 2021,
            "cor": "BRANCA",
            "combustivel": "DIESEL",
            "situacao": "REGULAR",
            "licenciamento": {
                "exercicio": 2025,
                "vencimento": "2025-09-30",
                "pago": True,
                "valor": "163.42",
            },
            "ipva": {
                "exercicio": 2025,
                "vencimento": "2025-04-30",
                "pago": False,
                "valor": "1234.56",
            },
            "multas": [
                {
                    "auto": "AIT-X1",
                    "data": "2025-08-15",
                    "valor": "195.23",
                    "descricao": "Excesso de velocidade",
                }
            ],
            "restricoes": [],
            "raw": None,
            "source": "infosimples",
        }

    async def aclose(self) -> None:  # pragma: no cover -- nao usamos
        return None


class FakeFailingClient:
    is_mock = False
    name = "infosimples"

    async def consultar_veiculo(self, placa: str, uf: str) -> dict[str, Any]:
        raise RuntimeError("upstream timeout")

    async def aclose(self) -> None:  # pragma: no cover
        return None


@pytest.fixture
def fake_ok_client():
    client = FakeOkClient()
    app.dependency_overrides[get_infosimples_dep] = lambda: client
    yield client
    app.dependency_overrides.pop(get_infosimples_dep, None)


@pytest.fixture
def fake_failing_client():
    client = FakeFailingClient()
    app.dependency_overrides[get_infosimples_dep] = lambda: client
    yield client
    app.dependency_overrides.pop(get_infosimples_dep, None)


async def _criar_veiculo(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    placa: str = "ABC1234",
) -> int:
    r = await api_client.post(
        "/api/v1/manutencao-frota/veiculos",
        json={"placa": placa, "renavam": "12345678900"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- endpoint POST /consultar-detran ----------------------------------------


@pytest.mark.asyncio
async def test_consultar_detran_sucesso(
    api_client: AsyncClient,
    db_session: AsyncSession,
    fake_ok_client: FakeOkClient,
    auth_headers: dict[str, str],
) -> None:
    vid = await _criar_veiculo(api_client, auth_headers)
    r = await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
        json={"uf": "sp"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["uf"] == "SP"
    assert body["placa"] == "ABC1234"
    assert body["status"] == "ok"
    assert body["source"] == "infosimples"
    assert body["payload"]["situacao"] == "REGULAR"
    assert len(body["payload"]["multas"]) == 1
    # client recebeu placa+uf normalizados
    assert fake_ok_client.calls == [("ABC1234", "SP")]


@pytest.mark.asyncio
async def test_consultar_detran_uf_invalida_retorna_422(
    api_client: AsyncClient, fake_ok_client: FakeOkClient, auth_headers: dict[str, str]
) -> None:
    vid = await _criar_veiculo(api_client, auth_headers)
    r = await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
        json={"uf": "RJ"},
        headers=auth_headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_consultar_detran_veiculo_nao_existe_retorna_404(
    api_client: AsyncClient, fake_ok_client: FakeOkClient, auth_headers: dict[str, str]
) -> None:
    r = await api_client.post(
        "/api/v1/manutencao-frota/veiculos/99999/consultar-detran",
        json={"uf": "SP"},
        headers=auth_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_consultar_detran_sem_credencial_retorna_mock(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    # Sem dependency override -- usa o singleton real, que cai em mock
    # porque INFOSIMPLES_TOKEN nao esta setado em testes.
    service.reset_infosimples_singleton()
    vid = await _criar_veiculo(api_client, auth_headers)
    try:
        r = await api_client.post(
            f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
            json={"uf": "SP"},
            headers=auth_headers,
        )
    finally:
        prev = service.reset_infosimples_singleton()
        if prev is not None:
            await prev.aclose()
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "mock"
    assert body["source"] == "infosimples_mock"


# --- erro / status 'erro' ---------------------------------------------------


@pytest.mark.asyncio
async def test_consultar_detran_erro_grava_row_status_erro(
    api_client: AsyncClient,
    db_session: AsyncSession,
    fake_failing_client: FakeFailingClient,
    auth_headers: dict[str, str],
) -> None:
    vid = await _criar_veiculo(api_client, auth_headers)
    r = await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
        json={"uf": "SP"},
        headers=auth_headers,
    )
    # Mesmo em erro de upstream, devolvemos 201 com status='erro' --
    # service NAO propaga para o router, deixa a UI renderizar inline.
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "erro"
    assert "upstream timeout" in (body.get("error_msg") or "")
    assert body["payload"] is None


# --- materializacao de documentos ------------------------------------------


@pytest.mark.asyncio
async def test_consultar_detran_materializa_ipva_e_licenciamento(
    api_client: AsyncClient,
    db_session: AsyncSession,
    fake_ok_client: FakeOkClient,
    auth_headers: dict[str, str],
) -> None:
    vid = await _criar_veiculo(api_client, auth_headers)
    r = await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
        json={"uf": "SP"},
        headers=auth_headers,
    )
    assert r.status_code == 201

    # Confirmamos via DB direto -- 2 documentos com source=detran_rpa
    q = select(DocumentoVeiculo).where(DocumentoVeiculo.veiculo_id == vid)
    docs = list((await db_session.execute(q)).scalars().all())
    docs_rpa = [d for d in docs if d.source == "detran_rpa"]
    tipos = {d.tipo for d in docs_rpa}
    assert tipos == {"ipva", "licenciamento"}
    # Validade do IPVA e do licenciamento veio do payload
    venc_por_tipo = {d.tipo: d.validade.isoformat() for d in docs_rpa}
    assert venc_por_tipo["ipva"] == "2025-04-30"
    assert venc_por_tipo["licenciamento"] == "2025-09-30"


@pytest.mark.asyncio
async def test_consultar_detran_payload_sem_datas_nao_cria_documentos(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    # Cliente que devolve payload sem ipva/licenciamento -- nao deve
    # criar rows em frota_documentos.
    client = FakeOkClient(
        payload={
            "uf": "MG",
            "placa": "ABC1234",
            "situacao": "REGULAR",
            "ipva": None,
            "licenciamento": None,
            "multas": [],
            "restricoes": [],
            "source": "infosimples",
        }
    )
    app.dependency_overrides[get_infosimples_dep] = lambda: client
    try:
        vid = await _criar_veiculo(api_client, auth_headers)
        r = await api_client.post(
            f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
            json={"uf": "MG"},
            headers=auth_headers,
        )
        assert r.status_code == 201
        q = select(DocumentoVeiculo).where(
            DocumentoVeiculo.veiculo_id == vid,
            DocumentoVeiculo.source == "detran_rpa",
        )
        docs = list((await db_session.execute(q)).scalars().all())
        assert docs == []
    finally:
        app.dependency_overrides.pop(get_infosimples_dep, None)


# --- audit log --------------------------------------------------------------


@pytest.mark.asyncio
async def test_consulta_grava_audit_log(
    api_client: AsyncClient,
    db_session: AsyncSession,
    fake_ok_client: FakeOkClient,
    auth_headers: dict[str, str],
) -> None:
    vid = await _criar_veiculo(api_client, auth_headers)
    await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
        json={"uf": "SP"},
        headers=auth_headers,
    )
    q = select(AuditLog).where(AuditLog.resource == "manutencao_frota.consulta_detran")
    rows = list((await db_session.execute(q)).scalars().all())
    assert len(rows) == 1
    assert rows[0].action == "create"


@pytest.mark.asyncio
async def test_consulta_erro_grava_audit_log_action_error(
    api_client: AsyncClient,
    db_session: AsyncSession,
    fake_failing_client: FakeFailingClient,
    auth_headers: dict[str, str],
) -> None:
    vid = await _criar_veiculo(api_client, auth_headers)
    await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
        json={"uf": "SP"},
        headers=auth_headers,
    )
    q = select(AuditLog).where(
        AuditLog.resource == "manutencao_frota.consulta_detran",
        AuditLog.action == "error",
    )
    rows = list((await db_session.execute(q)).scalars().all())
    assert len(rows) == 1


# --- listagem ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_listar_consultas_por_veiculo(
    api_client: AsyncClient,
    fake_ok_client: FakeOkClient,
    auth_headers: dict[str, str],
) -> None:
    vid = await _criar_veiculo(api_client, auth_headers)
    for _ in range(3):
        r = await api_client.post(
            f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
            json={"uf": "SP"},
            headers=auth_headers,
        )
        assert r.status_code == 201

    r = await api_client.get(f"/api/v1/manutencao-frota/veiculos/{vid}/consultas")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


@pytest.mark.asyncio
async def test_listar_consultas_global_filtra_por_uf(
    api_client: AsyncClient,
    fake_ok_client: FakeOkClient,
    auth_headers: dict[str, str],
) -> None:
    v1 = await _criar_veiculo(api_client, auth_headers, "ABC1234")
    v2 = await _criar_veiculo(api_client, auth_headers, "XYZ9876")
    await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{v1}/consultar-detran",
        json={"uf": "SP"},
        headers=auth_headers,
    )
    await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{v2}/consultar-detran",
        json={"uf": "MG"},
        headers=auth_headers,
    )

    r = await api_client.get("/api/v1/manutencao-frota/consultas-detran?uf=SP")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["uf"] == "SP"


@pytest.mark.asyncio
async def test_listar_consultas_veiculo_404_se_inexistente(
    api_client: AsyncClient, fake_ok_client: FakeOkClient
) -> None:
    r = await api_client.get("/api/v1/manutencao-frota/veiculos/99999/consultas")
    assert r.status_code == 404


# --- regressao: consultas nao quebram veiculo (cascade) ---------------------


@pytest.mark.asyncio
async def test_delete_veiculo_remove_consultas_em_cascata(
    api_client: AsyncClient,
    db_session: AsyncSession,
    fake_ok_client: FakeOkClient,
    auth_headers: dict[str, str],
) -> None:
    vid = await _criar_veiculo(api_client, auth_headers)
    await api_client.post(
        f"/api/v1/manutencao-frota/veiculos/{vid}/consultar-detran",
        json={"uf": "SP"},
        headers=auth_headers,
    )
    # Confirmar 1 row
    total = (await db_session.execute(select(func.count(ConsultaDetran.id)))).scalar_one()
    assert total == 1

    r = await api_client.delete(f"/api/v1/manutencao-frota/veiculos/{vid}", headers=auth_headers)
    assert r.status_code == 204

    total = (await db_session.execute(select(func.count(ConsultaDetran.id)))).scalar_one()
    assert total == 0
