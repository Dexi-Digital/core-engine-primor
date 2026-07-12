"""Testes do adapter OnSafety (Modulo A -- SST)."""
from __future__ import annotations

import json

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app.integrations.onsafety.client import (
    OnsafetyAuthError,
    OnsafetyClient,
    OnsafetyError,
    normalize_cpf,
)

# --- helpers ---------------------------------------------------------------


def _real_client(handler):
    transport = MockTransport(handler)
    http = AsyncClient(
        transport=transport, base_url="https://api.dev.onsafety.com.br"
    )
    return OnsafetyClient(api_token="t-real", client=http)


def _spring_page(content, total):
    return Response(
        200, json={"content": content, "totalElements": total}
    )


# --- normalize_cpf ----------------------------------------------------------


def test_normalize_cpf_remove_pontuacao():
    assert normalize_cpf("529.982.247-25") == "52998224725"
    assert normalize_cpf("52998224725") == "52998224725"
    assert normalize_cpf("") == ""


# --- mock client (sem token) -----------------------------------------------


@pytest.mark.asyncio
async def test_mock_e_deterministico():
    c1 = OnsafetyClient(api_token=None)
    c2 = OnsafetyClient(api_token="")
    r1 = await c1.list_trabalhadores(page=0, size=5)
    r2 = await c2.list_trabalhadores(page=0, size=5)
    assert r1 == r2
    assert r1["source"] == "onsafety_mock"
    assert len(r1["items"]) == 5
    assert r1["total"] == 12


@pytest.mark.asyncio
async def test_mock_paginacao_ultima_pagina_parcial():
    c = OnsafetyClient(api_token=None)
    ultima = await c.list_trabalhadores(page=2, size=5)  # 12 itens: 5+5+2
    assert len(ultima["items"]) == 2
    alem = await c.list_trabalhadores(page=3, size=5)
    assert alem["items"] == []


@pytest.mark.asyncio
async def test_mock_itens_estaveis_entre_paginacoes():
    # O item de indice global N deve ser o mesmo em qualquer size.
    c = OnsafetyClient(api_token=None)
    size3 = await c.list_trabalhadores(page=2, size=3)  # indices 6,7,8
    size7 = await c.list_trabalhadores(page=0, size=7)  # indices 0..6
    assert size3["items"][0] == size7["items"][6]


@pytest.mark.asyncio
async def test_mock_count_bate_com_total():
    c = OnsafetyClient(api_token=None)
    assert await c.count_trabalhadores() == 12


@pytest.mark.asyncio
async def test_mock_exames_shape():
    c = OnsafetyClient(api_token=None)
    r = await c.list_exames_ocupacionais(page=0, size=3)
    item = r["items"][0]
    assert set(item) == {
        "id",
        "tipo_exame",
        "data_aso",
        "data_vencimento_aso",
        "resultado_aso",
        "situacao",
        "ativo",
        "trabalhador",
    }
    assert len(item["trabalhador"]["cpf"]) == 11


@pytest.mark.asyncio
async def test_mock_controles_epi_e_treinamentos():
    c = OnsafetyClient(api_token=None)
    epis = await c.list_controles_epi(page=0, size=100)
    assert epis["total"] == 15 and len(epis["items"]) == 15
    assert epis["items"][0]["nome_equipamento"]
    trs = await c.list_treinamentos_trabalhadores(page=0, size=100)
    assert trs["total"] == 6
    assert trs["items"][0]["descricao"].startswith("NR-")


@pytest.mark.asyncio
async def test_mock_create_or_update_deterministico():
    c = OnsafetyClient(api_token=None)
    r1 = await c.create_or_update_trabalhador(
        nome="JOSE DA SILVA", cpf="529.982.247-25", codigo_externo="emp-1"
    )
    r2 = await c.create_or_update_trabalhador(
        nome="JOSE DA SILVA", cpf="52998224725", codigo_externo="emp-1"
    )
    assert r1["id"] == r2["id"]
    assert r1["id"].startswith("mock-")
    assert r1["cpf"] == "52998224725"
    assert r1["source"] == "onsafety_mock"


@pytest.mark.asyncio
async def test_mock_health_check_sempre_ok():
    c = OnsafetyClient(api_token=None)
    assert await c.health_check() is True


# --- validacao de input -----------------------------------------------------


@pytest.mark.asyncio
async def test_create_or_update_cpf_invalido():
    c = OnsafetyClient(api_token=None)
    with pytest.raises(ValueError):
        await c.create_or_update_trabalhador(
            nome="X", cpf="123", codigo_externo="emp-1"
        )


@pytest.mark.asyncio
async def test_create_or_update_campos_vazios():
    c = OnsafetyClient(api_token=None)
    with pytest.raises(ValueError):
        await c.create_or_update_trabalhador(
            nome="  ", cpf="52998224725", codigo_externo="emp-1"
        )
    with pytest.raises(ValueError):
        await c.create_or_update_trabalhador(
            nome="X", cpf="52998224725", codigo_externo=" "
        )


# --- client real (MockTransport) ---------------------------------------------


@pytest.mark.asyncio
async def test_real_list_trabalhadores_normaliza():
    def handler(request: Request) -> Response:
        assert request.headers["token"] == "t-real"
        assert request.url.params["fields"]  # projecao obrigatoria
        return _spring_page(
            [
                {
                    "id": "u-1",
                    "nome": "ADAIR DOS SANTOS",
                    "cpf": "529.982.247-25",
                    "matricula": "M1",
                    "dataAdmissao": "2023-06-21T00:00:00",
                    "codigoExterno": "emp-9",
                    "ativo": True,
                }
            ],
            5306,
        )

    c = _real_client(handler)
    r = await c.list_trabalhadores(page=0, size=1)
    assert r["total"] == 5306
    assert r["source"] == "onsafety"
    item = r["items"][0]
    assert item["cpf"] == "52998224725"
    assert item["data_admissao"] == "2023-06-21"
    assert item["codigo_externo"] == "emp-9"


@pytest.mark.asyncio
async def test_real_exame_normaliza_datas_e_trabalhador():
    def handler(request: Request) -> Response:
        return _spring_page(
            [
                {
                    "id": "e-1",
                    "tipoExameString": "Periódico",
                    "dataAso": "2025-03-01T00:00:00",
                    "dataVencimentoAso": "2026-03-01T00:00:00",
                    "resultadoAso": 1,
                    "situacao": "VALIDO",
                    "ativo": True,
                    "trabalhador": {
                        "id": "u-1",
                        "nome": "ADAIR",
                        "cpf": "52998224725",
                    },
                }
            ],
            1,
        )

    c = _real_client(handler)
    r = await c.list_exames_ocupacionais()
    item = r["items"][0]
    assert item["data_vencimento_aso"] == "2026-03-01"
    assert item["trabalhador"]["id"] == "u-1"


@pytest.mark.asyncio
async def test_real_count_usa_total_elements():
    def handler(request: Request) -> Response:
        # `/contar` esta quebrado na OnSafety; o client NUNCA deve chama-lo.
        assert not request.url.path.endswith("/contar")
        assert request.url.params["size"] == "1"
        return _spring_page([{"id": "u-1"}], 5306)

    c = _real_client(handler)
    assert await c.count_trabalhadores() == 5306


@pytest.mark.asyncio
async def test_real_401_levanta_auth_error_com_dica_de_ambiente():
    def handler(request: Request) -> Response:
        return Response(401, text="Usuário ou senha incorretos")

    c = _real_client(handler)
    with pytest.raises(OnsafetyAuthError) as exc:
        await c.list_trabalhadores()
    assert "ambiente" in str(exc.value)


@pytest.mark.asyncio
async def test_real_409_levanta_onsafety_error():
    def handler(request: Request) -> Response:
        return Response(
            409, text='Obrigatória utilização do parâmetro de consulta "fields"'
        )

    c = _real_client(handler)
    with pytest.raises(OnsafetyError):
        await c.list_trabalhadores()


@pytest.mark.asyncio
async def test_real_resposta_sem_content_levanta():
    def handler(request: Request) -> Response:
        return Response(200, json={"unexpected": True})

    c = _real_client(handler)
    with pytest.raises(OnsafetyError):
        await c.list_trabalhadores()


@pytest.mark.asyncio
async def test_real_create_or_update_monta_body_camel_case():
    captured: dict = {}

    def handler(request: Request) -> Response:
        captured["params"] = dict(request.url.params)
        captured["body"] = json.loads(request.content)
        return Response(200, json={"id": "u-novo"})

    c = _real_client(handler)
    r = await c.create_or_update_trabalhador(
        nome="JOSE DA SILVA",
        cpf="529.982.247-25",
        codigo_externo="emp-1",
        matricula="M42",
        data_admissao="2026-07-01",
        is_editing=False,
    )
    assert r["id"] == "u-novo"
    assert r["source"] == "onsafety"
    assert captured["params"]["isEditing"] == "false"
    body = captured["body"]
    assert body["cpf"] == "52998224725"
    assert body["codigoExterno"] == "emp-1"
    assert body["dataAdmissao"] == "2026-07-01T00:00:00"
    assert "dataNascimento" not in body


@pytest.mark.asyncio
async def test_real_health_check_ok_e_falha():
    def ok(request: Request) -> Response:
        return _spring_page([], 5306)

    def down(request: Request) -> Response:
        return Response(500, text="boom")

    assert await _real_client(ok).health_check() is True
    assert await _real_client(down).health_check() is False
