"""Testes do adapter OnSafety (Modulo A -- SST)."""
from __future__ import annotations

import json

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app.integrations.onsafety.client import (
    OnsafetyAuthError,
    OnsafetyClient,
    OnsafetyError,
    OnsafetyProdWriteBlockedError,
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
    assert r1["total"] == 10  # 12 no dataset, 2 inativos (default so ativos)


@pytest.mark.asyncio
async def test_mock_paginacao_ultima_pagina_parcial():
    c = OnsafetyClient(api_token=None)
    # ativo=None -> dataset completo (12 itens: 5+5+2)
    ultima = await c.list_trabalhadores(page=2, size=5, ativo=None)
    assert len(ultima["items"]) == 2
    alem = await c.list_trabalhadores(page=3, size=5, ativo=None)
    assert alem["items"] == []


@pytest.mark.asyncio
async def test_mock_itens_estaveis_entre_paginacoes():
    # O item de indice global N deve ser o mesmo em qualquer size.
    c = OnsafetyClient(api_token=None)
    size3 = await c.list_trabalhadores(page=2, size=3, ativo=None)
    size7 = await c.list_trabalhadores(page=0, size=7, ativo=None)
    assert size3["items"][0] == size7["items"][6]


@pytest.mark.asyncio
async def test_mock_filtro_ativo_soft_delete():
    # OnSafety faz soft-delete e a listagem padrao deles INCLUI
    # excluidos; nosso default ativo=True protege o pull disso.
    c = OnsafetyClient(api_token=None)
    default = await c.list_trabalhadores(page=0, size=100)
    todos = await c.list_trabalhadores(page=0, size=100, ativo=None)
    inativos = await c.list_trabalhadores(page=0, size=100, ativo=False)
    assert default["total"] == 10
    assert todos["total"] == 12
    assert inativos["total"] == 2
    assert all(it["ativo"] for it in default["items"])
    assert await c.count_trabalhadores() == 10
    assert await c.count_trabalhadores(ativo=None) == 12


@pytest.mark.asyncio
async def test_mock_filtro_nome_pre_paginacao():
    # Filtro aplicado ANTES da paginacao: total reflete o filtrado
    # (mesma semantica do Spring no lado real).
    c = OnsafetyClient(api_token=None)
    r = await c.list_trabalhadores(page=0, size=2, nome="JOSE", ativo=None)
    assert r["total"] == 2  # JOSE aparece 2x no dataset de 12
    assert all("JOSE" in it["nome"] for it in r["items"])


@pytest.mark.asyncio
async def test_mock_cpfs_passam_no_validador_do_repo():
    # A Squad 2 valida CPF (dp_sesmt/cpf.py) antes do matching; CPFs
    # mock com verificadores errados descartariam o dataset em silencio.
    from app.modules.dp_sesmt.cpf import is_valid_cpf

    c = OnsafetyClient(api_token=None)
    trabalhadores = await c.list_trabalhadores(page=0, size=100)
    exames = await c.list_exames_ocupacionais(page=0, size=100)
    cpfs = [i["cpf"] for i in trabalhadores["items"]] + [
        i["trabalhador"]["cpf"] for i in exames["items"]
    ]
    assert cpfs and all(is_valid_cpf(cpf) for cpf in cpfs)


@pytest.mark.asyncio
async def test_mock_count_bate_com_total():
    c = OnsafetyClient(api_token=None)
    lst = await c.list_trabalhadores(page=0, size=1)
    assert await c.count_trabalhadores() == lst["total"]


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
async def test_real_403_e_erro_de_negocio_nao_auth():
    # OnSafety usa 403 para validacao de negocio (ex.: push sem projeto
    # vinculado -> "Estabelecimento não especificado", visto no smoke em
    # homolog). Auth de fato e sempre 401.
    def handler(request: Request) -> Response:
        return Response(
            403, text="Não foi possível salvar o trabalhador X(null). "
            "Estabelecimento não especificado (null)"
        )

    c = _real_client(handler)
    with pytest.raises(OnsafetyError) as exc:
        await c.create_or_update_trabalhador(
            nome="X", cpf="52998224725", codigo_externo="emp-1"
        )
    assert not isinstance(exc.value, OnsafetyAuthError)
    assert "Estabelecimento" in str(exc.value)


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
        projeto_id="proj-99",
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
    assert body["projeto"] == {"id": "proj-99"}
    assert body["dataAdmissao"] == "2026-07-01T00:00:00"
    assert "dataNascimento" not in body

    # sem projeto_id o campo nao vai no body (OnSafety recusa com 403
    # de negocio -- coberto em test_real_403_e_erro_de_negocio_nao_auth)
    await c.create_or_update_trabalhador(
        nome="JOSE DA SILVA", cpf="52998224725", codigo_externo="emp-1"
    )
    assert "projeto" not in captured["body"]


@pytest.mark.asyncio
async def test_real_create_corpo_vazio_busca_id_por_cpf():
    # Sucesso real vem com 200 e corpo VAZIO (smoke homolog 2026-07-13);
    # o adapter deve buscar o id via filtro por CPF FORMATADO.
    def handler(request: Request) -> Response:
        if request.method == "POST":
            return Response(200, text="")
        assert request.url.params["cpf"] == "529.982.247-25"
        return _spring_page(
            [{"id": "u-real", "cpf": "529.982.247-25", "ativo": True}], 1
        )

    c = _real_client(handler)
    r = await c.create_or_update_trabalhador(
        nome="X", cpf="52998224725", codigo_externo="emp-1"
    )
    assert r["id"] == "u-real"


@pytest.mark.asyncio
async def test_real_find_by_cpf_formata_e_normaliza():
    def handler(request: Request) -> Response:
        assert request.url.params["cpf"] == "529.982.247-25"
        return _spring_page(
            [{"id": "u-1", "nome": "A", "cpf": "529.982.247-25"}], 1
        )

    c = _real_client(handler)
    found = await c.find_trabalhador_by_cpf("52998224725")
    assert found["id"] == "u-1"
    assert found["cpf"] == "52998224725"  # normalizado na saida


@pytest.mark.asyncio
async def test_real_find_by_cpf_inexistente_devolve_none():
    def handler(request: Request) -> Response:
        return _spring_page([], 0)

    c = _real_client(handler)
    assert await c.find_trabalhador_by_cpf("529.982.247-25") is None


@pytest.mark.asyncio
async def test_mock_find_by_cpf_consistente_com_create():
    c = OnsafetyClient(api_token=None)
    created = await c.create_or_update_trabalhador(
        nome="X", cpf="52998224725", codigo_externo="emp-1"
    )
    found = await c.find_trabalhador_by_cpf("529.982.247-25")
    assert found["id"] == created["id"]


# --- guard de escrita em producao -------------------------------------------


def _prod_client(handler=None, **kwargs):
    transport = MockTransport(
        handler or (lambda req: Response(200, json={"id": "u-1"}))
    )
    http = AsyncClient(
        transport=transport, base_url="https://api.onsafety.com.br"
    )
    return OnsafetyClient(api_token="t-prod", client=http, **kwargs)


@pytest.mark.asyncio
async def test_escrita_em_prod_bloqueada_por_default():
    c = _prod_client()
    with pytest.raises(OnsafetyProdWriteBlockedError) as exc:
        await c.create_or_update_trabalhador(
            nome="X", cpf="52998224725", codigo_externo="emp-1"
        )
    assert "ONSAFETY_ALLOW_PROD_WRITE" in str(exc.value)


@pytest.mark.asyncio
async def test_escrita_em_prod_liberada_com_opt_in():
    c = _prod_client(allow_prod_write=True)
    r = await c.create_or_update_trabalhador(
        nome="X", cpf="52998224725", codigo_externo="emp-1"
    )
    assert r["id"] == "u-1"


@pytest.mark.asyncio
async def test_leitura_em_prod_nao_e_bloqueada():
    def handler(request: Request) -> Response:
        return _spring_page([], 5306)

    c = _prod_client(handler)
    assert (await c.list_trabalhadores())["total"] == 5306


@pytest.mark.asyncio
async def test_escrita_em_homolog_nao_e_bloqueada():
    def handler(request: Request) -> Response:
        return Response(200, json={"id": "u-dev"})

    c = _real_client(handler)  # base_url api.dev.*
    r = await c.create_or_update_trabalhador(
        nome="X", cpf="52998224725", codigo_externo="emp-1"
    )
    assert r["id"] == "u-dev"


@pytest.mark.asyncio
async def test_real_health_check_ok_e_falha():
    def ok(request: Request) -> Response:
        return _spring_page([], 5306)

    def down(request: Request) -> Response:
        return Response(500, text="boom")

    assert await _real_client(ok).health_check() is True
    assert await _real_client(down).health_check() is False


# --- treinamentos: campos de data e NR (issue #39) --------------------------
# Os campos abaixo foram confirmados no spec OpenAPI publico da OnSafety
# (`GET /v3/api-docs`, schema `TreinamentoRealizado`) em 08/09/2026.


def test_fields_treinamentos_realizados_traz_validade_e_participantes():
    """A projecao PRECISA trazer vencimento -- sem ele o doc de NR
    entra com validade None, e o diagnostico trata None como
    "perene -> OK" (falso compliance de NR vencida).

    E precisa vir de `/v2/treinamentos_realizados`: pelo endpoint de
    participacoes a relacao `treinamentoRealizado` volta VAZIA contra a
    base real (medido em 11/09/2026)."""
    from app.integrations.onsafety.client import (
        FIELDS_TREINAMENTOS,
        FIELDS_TREINAMENTOS_REALIZADOS,
    )

    for campo in (
        "dataFim",
        "dataVencimento",
        "validadeDias",
        "sigla",
        "trabalhadores.aprovado",
        "trabalhadores.trabalhador.cpf",
    ):
        assert campo in FIELDS_TREINAMENTOS_REALIZADOS, campo

    assert "treinamentoRealizado" not in FIELDS_TREINAMENTOS, (
        "pedir a relacao vazia no endpoint de participacoes so gera ruido"
    )


@pytest.mark.asyncio
async def test_mock_treinamento_traz_datas_sigla_e_projeto():
    c = OnsafetyClient(api_token=None)
    trs = await c.list_treinamentos_trabalhadores(page=0, size=100)
    item = trs["items"][0]
    assert set(item) == {
        "id",
        "descricao",
        "sigla",
        "grupo",
        "aprovado",
        "renovado",
        "certificado_id",
        "data_fim",
        "data_vencimento",
        "validade_dias",
        "situacao",
        "projeto",
        "ativo",
        "trabalhador",
    }
    assert item["data_fim"] and item["data_vencimento"]
    assert item["projeto"]["codigo_externo"]


def test_normalize_treinamento_datas_iso_e_projeto():
    c = OnsafetyClient(api_token=None)
    raw = {
        "id": "t-1",
        "aprovado": True,
        "renovado": False,
        "certificateId": "c-1",
        "ativo": True,
        "treinamentoRealizado": {
            "id": "tr-1",
            "descricao": "NR-35 Trabalho em Altura",
            "sigla": "NR-35",
            "dataFim": "2026-03-10T00:00:00.000+00:00",
            "dataVencimento": "2028-03-10T00:00:00.000+00:00",
            "validadeDias": 730,
            "situacao": "CONCLUIDO",
            "treinamentoCodigo": {"grupo": "NR-35", "codigo": 35},
            "establishment": {
                "id": "p-1",
                "nome": "OBRA 243 - GUAXIMA",
                "codigoExterno": "243",
            },
        },
        "trabalhador": {"id": "w-1", "nome": "X", "cpf": "529.982.247-25"},
    }
    item = c._normalize_treinamento(raw)
    assert item["data_fim"] == "2026-03-10"
    assert item["data_vencimento"] == "2028-03-10"
    assert item["validade_dias"] == 730
    assert item["sigla"] == "NR-35"
    assert item["grupo"] == "NR-35"
    assert item["projeto"] == {
        "id": "p-1",
        "nome": "OBRA 243 - GUAXIMA",
        "codigo_externo": "243",
    }
    assert item["trabalhador"]["cpf"] == "52998224725"


@pytest.mark.asyncio
async def test_mock_cpfs_cruzam_entre_datasets():
    """Follow-up da #39: os CPFs de exames/EPI/treinamentos precisam
    existir no dataset `trabalhadores`, senao nenhum teste de pull
    exercita o caminho de match real."""
    c = OnsafetyClient(api_token=None)
    trab = await c.list_trabalhadores(page=0, size=100, ativo=None)
    base = {i["cpf"] for i in trab["items"]}
    assert base
    for chamada in (
        c.list_exames_ocupacionais,
        c.list_controles_epi,
        c.list_treinamentos_trabalhadores,
    ):
        res = await chamada(page=0, size=100)
        cpfs = {i["trabalhador"]["cpf"] for i in res["items"]}
        assert cpfs and cpfs <= base, chamada.__name__
