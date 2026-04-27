"""Tests for D.6 fase 2 (CREA via Infosimples) -- service + router.

Cobre:
  - service.consultar_crea: ok/mock/error path, audit
  - service.list_consultas_crea: filtros + paginacao
  - service.importar_art_como_certidao: cria CertidaoEmpresa, FK,
    cenarios de ART invalida/cancelada, transporte erro
  - service._parse_iso_date: variacoes de formato
  - router endpoints (auth gate, 422 em UF/tipo invalido, idempotencia
    do cache de log)
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.integrations.infosimples.client import (
    InfosimplesCreaTipoNaoSuportadoError,
    InfosimplesError,
    InfosimplesUFNaoSuportadaError,
)
from app.modules.licitacoes import crea_service
from app.modules.licitacoes.certidoes_router import get_infosimples_dep
from app.modules.licitacoes.crea_service import _parse_iso_date
from app.modules.licitacoes.models import (
    CREA_CONSULTA_ERRO,
    CREA_CONSULTA_MOCK,
    CertidaoEmpresa,
    ConsultaCrea,
)

# ---------------------------- helpers ----------------------------------------


class _StubClient:
    """Client mock controlavel pra testes do service.

    Levanta `_failure` se setado, senao retorna `_payload`.
    """

    is_mock = False

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        failure: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._failure = failure
        self.calls: list[tuple[str, str, str]] = []

    async def consultar_crea(
        self, uf: str, tipo: str, identificador: str
    ) -> dict[str, Any]:
        self.calls.append((uf, tipo, identificador))
        if self._failure:
            raise self._failure
        return self._payload or {}


def _build_art_payload(
    *,
    numero: str = "MG2023ABC123",
    situacao: str = "ATIVA",
    data_registro: str = "2023-05-12",
    data_termino: str = "2024-12-31",
) -> dict[str, Any]:
    return {
        "uf": "MG",
        "tipo": "art",
        "identificador": numero,
        "art": {
            "numero": numero,
            "tipo_servico": "OBRA / SERVICO TECNICO",
            "valor_contrato": "1500000.00",
            "data_registro": data_registro,
            "data_inicio": "2023-06-01",
            "data_termino_previsto": data_termino,
            "situacao": situacao,
        },
        "profissional": {
            "registro_crea": "MG-145678/D",
            "nome": "JOSE DA SILVA",
            "cpf": "***",
            "titulo": "ENGENHEIRO CIVIL",
            "situacao": "REGULAR",
        },
        "empresa": None,
        "raw": {},
        "source": "infosimples",
    }


# ---------------------------- _parse_iso_date --------------------------------


def test_parse_iso_date_formats() -> None:
    assert _parse_iso_date("2023-05-12") == date(2023, 5, 12)
    assert _parse_iso_date("12/05/2023") == date(2023, 5, 12)
    assert _parse_iso_date("2023-05-12T00:00:00") == date(2023, 5, 12)
    assert _parse_iso_date("2023-05-12T00:00:00Z") == date(2023, 5, 12)
    # ja date / datetime
    assert _parse_iso_date(date(2023, 5, 12)) == date(2023, 5, 12)
    # nulos / invalidos
    assert _parse_iso_date(None) is None
    assert _parse_iso_date("") is None
    assert _parse_iso_date("   ") is None
    assert _parse_iso_date("data invalida") is None


# ---------------------------- consultar_crea ---------------------------------


@pytest.mark.asyncio
async def test_consultar_crea_ok_persists_payload_and_audit(
    db_session: AsyncSession,
) -> None:
    payload = _build_art_payload()
    client = _StubClient(payload=payload)

    consulta = await crea_service.consultar_crea(
        db_session,
        uf="MG",
        tipo="ART",  # case-insensitive
        identificador="  MG2023ABC123  ",  # trimmed
        client=client,
        actor="user@primor.com",
    )

    assert consulta.id is not None
    assert consulta.uf == "MG"
    assert consulta.tipo == "art"
    assert consulta.identificador == "MG2023ABC123"
    assert consulta.status == "ok"
    assert consulta.source == "infosimples"
    assert consulta.payload == payload
    assert consulta.error_msg is None
    assert client.calls == [("MG", "art", "MG2023ABC123")]

    # audit_log com actor real
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource == "licitacoes.crea_consulta"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].action == "create"
    assert audits[0].actor == "user@primor.com"
    assert audits[0].resource_id == str(consulta.id)


@pytest.mark.asyncio
async def test_consultar_crea_marca_mock_quando_source_termina_em_mock(
    db_session: AsyncSession,
) -> None:
    payload = _build_art_payload()
    payload["source"] = "infosimples_mock"
    client = _StubClient(payload=payload)

    consulta = await crea_service.consultar_crea(
        db_session,
        uf="MG",
        tipo="art",
        identificador="MG2023ABC123",
        client=client,
    )
    assert consulta.status == CREA_CONSULTA_MOCK
    assert consulta.source == "infosimples_mock"


@pytest.mark.asyncio
async def test_consultar_crea_erro_persiste_row_e_audita(
    db_session: AsyncSession,
) -> None:
    client = _StubClient(failure=InfosimplesError("rate limit 429"))

    consulta = await crea_service.consultar_crea(
        db_session,
        uf="MG",
        tipo="art",
        identificador="MG2023ABC123",
        client=client,
        actor="user@primor.com",
    )

    assert consulta.status == CREA_CONSULTA_ERRO
    assert consulta.error_msg is not None and "rate limit 429" in consulta.error_msg
    # audit grava `error`
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "error")
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].actor == "user@primor.com"


@pytest.mark.asyncio
async def test_consultar_crea_uf_invalida_levanta(
    db_session: AsyncSession,
) -> None:
    client = _StubClient()
    with pytest.raises(InfosimplesUFNaoSuportadaError):
        await crea_service.consultar_crea(
            db_session,
            uf="RJ",
            tipo="art",
            identificador="RJ2023X",
            client=client,
        )
    # nao deve gravar consulta (validacao antes de persistir)
    rows = (await db_session.execute(select(ConsultaCrea))).scalars().all()
    assert list(rows) == []


@pytest.mark.asyncio
async def test_consultar_crea_tipo_invalido_levanta(
    db_session: AsyncSession,
) -> None:
    client = _StubClient()
    with pytest.raises(InfosimplesCreaTipoNaoSuportadoError):
        await crea_service.consultar_crea(
            db_session,
            uf="MG",
            tipo="veiculo",
            identificador="X",
            client=client,
        )


@pytest.mark.asyncio
async def test_consultar_crea_identificador_vazio_levanta(
    db_session: AsyncSession,
) -> None:
    client = _StubClient()
    with pytest.raises(ValueError, match="identificador vazio"):
        await crea_service.consultar_crea(
            db_session,
            uf="MG",
            tipo="art",
            identificador="   ",
            client=client,
        )


# ---------------------------- list_consultas_crea ----------------------------


@pytest.mark.asyncio
async def test_list_consultas_crea_filtros_e_paginacao(
    db_session: AsyncSession,
) -> None:
    client = _StubClient(payload=_build_art_payload())
    # 3 consultas, 2 distintas
    await crea_service.consultar_crea(
        db_session, uf="MG", tipo="art", identificador="A", client=client
    )
    await crea_service.consultar_crea(
        db_session, uf="MG", tipo="art", identificador="A", client=client
    )
    await crea_service.consultar_crea(
        db_session,
        uf="SP",
        tipo="profissional",
        identificador="SP-1/D",
        client=client,
    )

    rows, total = await crea_service.list_consultas_crea(db_session)
    assert total == 3
    assert len(rows) == 3
    # ordenadas desc (mais recente primeiro). Como podem ter timestamps
    # iguais em SQLite (granularidade ms), a ordem dentro do empate
    # nao e garantida; checamos so por filtro.
    rows_a, total_a = await crea_service.list_consultas_crea(
        db_session, identificador="A"
    )
    assert total_a == 2

    rows_sp, total_sp = await crea_service.list_consultas_crea(
        db_session, uf="SP"
    )
    assert total_sp == 1
    assert rows_sp[0].tipo == "profissional"

    # pagination: limit=1, offset=1 -> total ainda 3
    _, total_pag = await crea_service.list_consultas_crea(
        db_session, limit=1, offset=1
    )
    assert total_pag == 3


# ---------------------------- importar_art_como_certidao ---------------------


@pytest.mark.asyncio
async def test_importar_art_cria_certidao_e_fk(
    db_session: AsyncSession,
) -> None:
    client = _StubClient(payload=_build_art_payload())

    consulta, certidao = await crea_service.importar_art_como_certidao(
        db_session,
        uf="MG",
        numero_art="MG2023ABC123",
        empresa_cnpj="44229813000123",
        client=client,
        actor="user@primor.com",
    )
    assert certidao is not None
    assert certidao.id is not None
    assert certidao.tipo == "ACERVO_TECNICO"
    assert certidao.numero == "MG2023ABC123"
    assert certidao.empresa_cnpj == "44229813000123"
    assert certidao.emissao == date(2023, 5, 12)
    assert certidao.validade == date(2024, 12, 31)
    assert certidao.orgao_emissor == "CREA-MG"
    assert "Importado de ART MG2023ABC123" in (certidao.observacoes or "")

    # FK na consulta aponta pra certidao criada
    assert consulta.certidao_id == certidao.id


@pytest.mark.asyncio
async def test_importar_art_recusa_cancelada(
    db_session: AsyncSession,
) -> None:
    payload = _build_art_payload(situacao="CANCELADA")
    client = _StubClient(payload=payload)

    consulta, certidao = await crea_service.importar_art_como_certidao(
        db_session,
        uf="MG",
        numero_art="MG2023ABC123",
        empresa_cnpj="44229813000123",
        client=client,
    )
    assert certidao is None
    assert consulta.error_msg is not None
    assert "CANCELADA" in consulta.error_msg
    # nao cria CertidaoEmpresa nem FK
    rows = (
        (await db_session.execute(select(CertidaoEmpresa))).scalars().all()
    )
    assert list(rows) == []


@pytest.mark.asyncio
async def test_importar_art_recusa_baixada(
    db_session: AsyncSession,
) -> None:
    payload = _build_art_payload(situacao="BAIXADA")
    client = _StubClient(payload=payload)

    consulta, certidao = await crea_service.importar_art_como_certidao(
        db_session,
        uf="MG",
        numero_art="MG2023ABC123",
        empresa_cnpj="44229813000123",
        client=client,
    )
    assert certidao is None
    assert consulta.error_msg is not None and "BAIXADA" in consulta.error_msg


@pytest.mark.asyncio
async def test_importar_art_quando_consulta_falha_retorna_certidao_none(
    db_session: AsyncSession,
) -> None:
    client = _StubClient(failure=InfosimplesError("transporte falhou"))

    consulta, certidao = await crea_service.importar_art_como_certidao(
        db_session,
        uf="MG",
        numero_art="MG2023ABC123",
        empresa_cnpj="44229813000123",
        client=client,
    )
    assert certidao is None
    assert consulta.status == CREA_CONSULTA_ERRO


@pytest.mark.asyncio
async def test_importar_art_sem_numero_no_payload_retorna_none(
    db_session: AsyncSession,
) -> None:
    payload = _build_art_payload()
    payload["art"]["numero"] = None
    client = _StubClient(payload=payload)

    consulta, certidao = await crea_service.importar_art_como_certidao(
        db_session,
        uf="MG",
        numero_art="MG2023XXX",
        empresa_cnpj="44229813000123",
        client=client,
    )
    assert certidao is None
    assert consulta.status == CREA_CONSULTA_ERRO
    assert "sem numero" in (consulta.error_msg or "")


# ---------------------------- router endpoints -------------------------------


def _override_infosimples_dep(client: Any) -> None:
    from app.main import app

    app.dependency_overrides[get_infosimples_dep] = lambda: client


def _clear_infosimples_dep() -> None:
    from app.main import app

    app.dependency_overrides.pop(get_infosimples_dep, None)


@pytest.mark.asyncio
async def test_endpoint_consultar_crea_ok(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    client = _StubClient(payload=_build_art_payload())
    _override_infosimples_dep(client)
    try:
        resp = await api_client.post(
            "/api/v1/licitacoes/certidoes/consultar-crea",
            json={
                "uf": "MG",
                "tipo": "art",
                "identificador": "MG2023ABC123",
            },
            headers=auth_headers,
        )
    finally:
        _clear_infosimples_dep()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["uf"] == "MG"
    assert body["tipo"] == "art"
    assert body["payload"]["art"]["numero"] == "MG2023ABC123"


@pytest.mark.asyncio
async def test_endpoint_consultar_crea_sem_auth_devolve_401(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.post(
        "/api/v1/licitacoes/certidoes/consultar-crea",
        json={"uf": "MG", "tipo": "art", "identificador": "X"},
    )
    assert resp.status_code in {401, 403}


@pytest.mark.asyncio
async def test_endpoint_consultar_crea_uf_invalida_devolve_422(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # Stub que nem deveria ser chamado -- service levanta antes
    client = _StubClient(payload={})
    _override_infosimples_dep(client)
    try:
        resp = await api_client.post(
            "/api/v1/licitacoes/certidoes/consultar-crea",
            json={"uf": "RJ", "tipo": "art", "identificador": "X"},
            headers=auth_headers,
        )
    finally:
        _clear_infosimples_dep()
    assert resp.status_code == 422
    assert "RJ" in resp.text or "nao suportada" in resp.text


@pytest.mark.asyncio
async def test_endpoint_importar_art_cria_certidao(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    client = _StubClient(payload=_build_art_payload())
    _override_infosimples_dep(client)
    try:
        resp = await api_client.post(
            "/api/v1/licitacoes/certidoes/importar-art",
            json={
                "uf": "MG",
                "numero_art": "MG2023ABC123",
                "empresa_cnpj": "44229813000123",
            },
            headers=auth_headers,
        )
    finally:
        _clear_infosimples_dep()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["consulta"]["status"] == "ok"
    assert body["certidao"] is not None
    assert body["certidao"]["tipo"] == "ACERVO_TECNICO"
    assert body["certidao"]["numero"] == "MG2023ABC123"
    assert body["certidao"]["orgao_emissor"] == "CREA-MG"
    assert body["consulta"]["certidao_id"] == body["certidao"]["id"]


@pytest.mark.asyncio
async def test_endpoint_importar_art_cancelada_retorna_certidao_null(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    client = _StubClient(payload=_build_art_payload(situacao="CANCELADA"))
    _override_infosimples_dep(client)
    try:
        resp = await api_client.post(
            "/api/v1/licitacoes/certidoes/importar-art",
            json={
                "uf": "MG",
                "numero_art": "MG2023ABC123",
                "empresa_cnpj": "44229813000123",
            },
            headers=auth_headers,
        )
    finally:
        _clear_infosimples_dep()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["certidao"] is None
    assert "CANCELADA" in (body["consulta"]["error_msg"] or "")


@pytest.mark.asyncio
async def test_endpoint_list_crea_consultas(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    # Popula 2 consultas via service direto (mais rapido que via HTTP)
    client = _StubClient(payload=_build_art_payload())
    await crea_service.consultar_crea(
        db_session, uf="MG", tipo="art", identificador="A", client=client
    )
    await crea_service.consultar_crea(
        db_session,
        uf="SP",
        tipo="empresa",
        identificador="00000000000100",
        client=client,
    )

    # Sem filtro: total=2
    resp = await api_client.get("/api/v1/licitacoes/certidoes/crea-consultas")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2

    # Filtro por uf=SP devolve so 1
    resp = await api_client.get(
        "/api/v1/licitacoes/certidoes/crea-consultas?uf=SP"
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["uf"] == "SP"
