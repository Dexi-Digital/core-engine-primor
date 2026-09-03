"""Testes do adapter Tangerino (ponto/Solides)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app.core.config import Settings
from app.integrations.tangerino.client import (
    TangerinoAuthError,
    TangerinoClient,
    TangerinoError,
)


def test_settings_defaults_tangerino_e_onvio():
    """Nenhuma credencial entra por default, e os valores fixos batem.

    As credenciais sao checadas por "falsy" e nao por `is None` de
    proposito: a fixture `_sem_credenciais_reais` do conftest zera essas
    variaveis com string vazia para isolar a suite das credenciais reais
    que hoje existem no `.env`. O que importa aqui e que nada
    autentique sozinho -- `None` e `""` levam os adapters ao mock do
    mesmo jeito.
    """
    s = Settings(_env_file=None)
    assert not s.tangerino_api_key
    assert not s.onvio_client_id
    assert not s.onvio_client_secret
    assert not s.onvio_integration_key

    assert s.tangerino_base_url == "https://employer.tangerino.com.br"
    assert s.onvio_audience == "409f91f6-dc17-44c8-a5d8-e0a1bafd8b67"
    assert s.onvio_allow_send is False


# --- mock (sem token) ------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_funcionarios_deterministico():
    c1 = TangerinoClient(api_token=None)
    c2 = TangerinoClient(api_token="")
    r1 = await c1.list_funcionarios()
    r2 = await c2.list_funcionarios()
    assert r1 == r2
    assert r1["source"] == "tangerino_mock"
    assert r1["total"] == 3
    assert len(r1["items"]) == 3


@pytest.mark.asyncio
async def test_mock_funcionarios_cpf_valido_e_workplaces_de_duas_obras():
    from app.core.cpf import is_valid_cpf

    c = TangerinoClient(api_token=None)
    r = await c.list_funcionarios()
    workplaces = set()
    for item in r["items"]:
        assert is_valid_cpf(item["cpf"])
        assert item["workplaces"], "funcionario mock sem workplace"
        workplaces.add(item["workplaces"][0]["nome"])
    assert len(workplaces) == 2  # 2 obras distintas no dataset


@pytest.mark.asyncio
async def test_mock_batidas_por_funcionario_e_estavel():
    c = TangerinoClient(api_token=None)
    func = (await c.list_funcionarios())["items"][0]
    r1 = await c.list_batidas(func["id"], start_date="2026-08-01", end_date="2026-08-05")
    r2 = await c.list_batidas(func["id"], start_date="2026-08-01", end_date="2026-08-05")
    assert r1 == r2
    assert r1["source"] == "tangerino_mock"
    assert r1["total"] > 0
    b = r1["items"][0]
    assert b["employee_id"] == func["id"]
    assert b["fim_ts"] > b["inicio_ts"]
    assert b["segundos_trabalhados"] > 0


@pytest.mark.asyncio
async def test_mock_locais_trabalho():
    c = TangerinoClient(api_token=None)
    r = await c.list_locais_trabalho()
    assert r["total"] == 2
    nomes = {w["nome"] for w in r["items"]}
    assert nomes == {"OBRA BR-040 LOTE 3", "OBRA MG-050 RECAPEAMENTO"}


@pytest.mark.asyncio
async def test_mock_health_check_true():
    c = TangerinoClient(api_token=None)
    assert await c.health_check() is True


# --- real (httpx MockTransport) --------------------------------------------


def _real_tangerino(handler):
    transport = MockTransport(handler)
    http = AsyncClient(transport=transport, base_url="https://employer.tangerino.com.br")
    return TangerinoClient(api_token="tok-123", client=http)


def _spring(content, total):
    return Response(200, json={"content": content, "totalElements": total})


@pytest.mark.asyncio
async def test_real_list_funcionarios_normaliza_e_auth_header():
    seen = {}

    def handler(request: Request) -> Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return _spring(
            [
                {
                    "id": 7,
                    "externalId": "E-7",
                    "name": "JOSE DA SILVA",
                    "cpf": "529.982.247-25",
                    "pis": "12345678901",
                    "admissionDate": "2024-02-01",
                    "fired": False,
                    "workplaceList": [{"id": 1, "externalId": "OB-1", "name": "OBRA X"}],
                }
            ],
            1,
        )

    c = _real_tangerino(handler)
    r = await c.list_funcionarios(page=0, size=50)
    assert seen["auth"] == "tok-123"
    assert seen["path"] == "/employee/find-all"
    # `page`/`size`: a API real IGNORA pageNumber/pageSize e devolve o
    # default de 20 itens. Confirmado com credencial em 28/08/2026.
    assert seen["params"]["page"] == "0"
    assert seen["params"]["size"] == "50"
    item = r["items"][0]
    assert item["cpf"] == "52998224725"
    assert item["workplaces"] == [{"id": 1, "external_id": "OB-1", "nome": "OBRA X"}]
    assert r["total"] == 1
    assert r["source"] == "tangerino"


@pytest.mark.asyncio
async def test_real_list_batidas_path_e_periodo():
    seen = {}

    def handler(request: Request) -> Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return _spring(
            [
                {
                    "employeeId": 7,
                    "employeeExternalId": "E-7",
                    "dateWorked": 1754006400000,
                    "startDateTimestamp": 1754032800000,
                    "endDateTimestamp": 1754065200000,
                    "workedTimeInSeconds": 28800,
                    "status": "CLOSED",
                    "pis": "12345678901",
                }
            ],
            1,
        )

    c = _real_tangerino(handler)
    r = await c.list_batidas(7, start_date="2026-08-01", end_date="2026-08-05")
    assert seen["path"] == "/external/api/v1/payssego/punches/7"
    # dd/MM/yyyy: a API real devolve 400 (BindException) para ISO.
    # Confirmado com credencial em 27/08/2026.
    assert seen["params"]["startDate"] == "01/08/2026"
    assert seen["params"]["endDate"] == "05/08/2026"
    b = r["items"][0]
    assert b["segundos_trabalhados"] == 28800
    assert b["inicio_ts"] == 1754032800000
    assert r["source"] == "tangerino"


@pytest.mark.asyncio
async def test_real_401_vira_auth_error():
    def handler(request: Request) -> Response:
        return Response(401, json={"error": "Unauthorized"})

    c = _real_tangerino(handler)
    with pytest.raises(TangerinoAuthError):
        await c.list_funcionarios()


@pytest.mark.asyncio
async def test_real_500_vira_tangerino_error():
    def handler(request: Request) -> Response:
        return Response(500, text="boom")

    c = _real_tangerino(handler)
    with pytest.raises(TangerinoError):
        await c.list_locais_trabalho()


@pytest.mark.asyncio
async def test_real_locais_trabalho_normaliza():
    def handler(request: Request) -> Response:
        assert request.url.path == "/workplace/find-all"
        return _spring(
            [
                {
                    "id": 1,
                    "externalId": "OB-1",
                    "name": "OBRA X",
                    "active": True,
                    "standard": False,
                }
            ],
            1,
        )

    c = _real_tangerino(handler)
    r = await c.list_locais_trabalho()
    assert r["items"] == [
        {
            "id": 1,
            "external_id": "OB-1",
            "nome": "OBRA X",
            "ativo": True,
            "padrao": False,
        }
    ]


# --------------------------------------------------- descobertas com token real
def test_datas_sao_convertidas_para_formato_brasileiro():
    """Confirmado contra a API REAL em 27/08/2026.

    O spec publico nao declara o formato. Testando com credencial:
      startDate=2026-07-28   -> HTTP 400 BindException (typeMismatch)
      startDate=28/07/2026   -> passou o binding (404 "cant find punches")

    Ou seja: dd/MM/yyyy. O adapter aceita ISO na interface (resto do
    repo usa ISO) e converte na borda.
    """
    from app.integrations.tangerino.client import to_tangerino_date

    assert to_tangerino_date("2026-07-28") == "28/07/2026"
    assert to_tangerino_date("28/07/2026") == "28/07/2026"


@pytest.mark.asyncio
async def test_list_batidas_envia_data_no_formato_aceito_pela_api():
    enviado: dict[str, str] = {}

    def handler(request: Request) -> Response:
        enviado.update(dict(request.url.params))
        return Response(200, json={"content": [], "totalElements": 0})

    c = _real_tangerino(handler)
    try:
        await c.list_batidas(123, start_date="2026-07-01", end_date="2026-07-31")
    finally:
        await c.aclose()

    assert enviado["startDate"] == "01/07/2026"
    assert enviado["endDate"] == "31/07/2026"


def test_paginacao_usa_page_e_size_nao_pagenumber():
    """Confirmado contra a API REAL em 28/08/2026.

        pageNumber=0&pageSize=5  -> 20 itens (ignorado, caiu no default)
        page=0&size=5            ->  5 itens (honrado)

    Com `pageNumber` o adapter receberia sempre 20 de 396 funcionarios.
    """
    seen: dict = {}

    def handler(request: Request) -> Response:
        seen.update(dict(request.url.params))
        return _spring([], 0)

    import asyncio

    c = _real_tangerino(handler)
    asyncio.run(c.list_funcionarios(page=0, size=500))
    assert seen["size"] == "500"
    assert seen["page"] == "0"
    assert "pageSize" not in seen
    assert "pageNumber" not in seen


@pytest.mark.asyncio
async def test_batidas_404_vira_lista_vazia_nao_erro():
    """A API responde 404 "Cant find punches for this employee" quando o
    funcionario nao tem batida no periodo -- observado contra a API real
    em 27/08/2026.

    Isso e ausencia de dado, nao falha. Se levantasse erro, o pull
    noturno abortaria inteiro no primeiro dos 396 funcionarios sem
    batida, e nenhum dos seguintes seria lido.
    """
    def handler(request: Request) -> Response:
        return Response(
            404,
            json={
                "timestamp": 1787883934030,
                "status": 404,
                "error": "Not Found",
                "exception": "br.com.tangerino.exception.NotFoundException",
                "message": "Cant find punches for this employee",
            },
        )

    c = _real_tangerino(handler)
    r = await c.list_batidas(7, start_date="2026-08-01", end_date="2026-08-31")
    assert r["items"] == []
    assert r["total"] == 0


@pytest.mark.asyncio
async def test_404_em_outro_endpoint_continua_sendo_erro():
    """So batidas tem essa semantica. 404 em funcionarios e problema."""
    def handler(request: Request) -> Response:
        return Response(404, text="nao existe")

    c = _real_tangerino(handler)
    with pytest.raises(TangerinoError):
        await c.list_funcionarios(page=0, size=10)
