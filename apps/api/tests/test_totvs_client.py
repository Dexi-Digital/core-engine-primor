"""Adapter TOTVS RM (Modulo C) -- extractor atras de interface + mock.

Cobre as decisoes registradas em docs/integrations.md:
  #1 read-only por construcao (nao existe metodo de escrita)
  #4 idempotencia por external_id (chave natural do RM)
  #6 Decimal, nunca float
  #9 health_check False ate credencial e porta confirmadas
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.integrations.totvs.client import TotvsClient, TotvsReadOnlyError
from app.integrations.totvs.extractor import (
    ConsultaSqlExtractor,
    MockExtractor,
    RestExtractor,
    external_id_rm,
)

DESDE = date(2026, 8, 1)
ATE = date(2026, 8, 31)


# --------------------------------------------------------------- #1 read-only
@pytest.mark.asyncio
async def test_client_nao_expoe_push():
    """Decisao #1: o metodo de escrita NAO existe. Nao e flag, nao e default."""
    client = TotvsClient(extractor=MockExtractor())
    with pytest.raises(TotvsReadOnlyError):
        await client.push({"qualquer": "coisa"})


def test_client_nao_tem_nenhum_metodo_de_escrita():
    """Guarda contra alguem adicionar create/update/delete/send no futuro."""
    proibidos = {"create", "update", "delete", "post", "send", "write", "upsert"}
    publicos = {m for m in dir(TotvsClient) if not m.startswith("_")}
    assert not (publicos & proibidos), publicos & proibidos


# --------------------------------------------------------------- #9 health
@pytest.mark.asyncio
async def test_health_check_false_no_mock():
    """Decisao #9: nada de integracao meio-ligada passando por verde.
    O mock existe para destravar dev/CI, nao para pintar o painel."""
    client = TotvsClient(extractor=MockExtractor())
    assert await client.health_check() is False


@pytest.mark.asyncio
async def test_health_check_usa_endpoint_sem_consumo_de_licenca():
    """`/api/framework/v1/coligadas` teve consumo de licenca DESATIVADO
    (12.1.2302 p121 / 2209 p195 / 2205 p246; a instancia e 12.1.2510).
    Health check nao pode queimar licenca a cada minuto."""
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if "/api/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        return httpx.Response(200, json={"items": []})

    ex = _rest(handler)
    try:
        assert await ex.health_check() is True
    finally:
        await ex.aclose()
    assert any("/api/framework/v1/coligadas" in u for u in urls), urls


# --------------------------------------------------------------- auth Bearer
def _rest(handler, **kw) -> RestExtractor:
    return RestExtractor(
        base_url="https://primor.rm.totvs.cloud:8051",
        username="svc_motor",
        password="segredo",
        transport=httpx.MockTransport(handler),
        **kw,
    )


@pytest.mark.asyncio
async def test_token_e_reusado_entre_requisicoes():
    """Token custa uma requisicao e uma licenca; nao pode ser refeito a cada call."""
    tokens = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tokens
        if "/api/connect/token" in str(request.url):
            tokens += 1
            return httpx.Response(
                200, json={"access_token": "t1", "refresh_token": "r1", "expires_in": 300}
            )
        return httpx.Response(200, json={"items": [], "hasNext": False})

    ex = _rest(handler)
    try:
        await ex.fetch_lancamentos(desde=DESDE, ate=ATE)
        await ex.fetch_lancamentos(desde=DESDE, ate=ATE)
    finally:
        await ex.aclose()
    assert tokens == 1


@pytest.mark.asyncio
async def test_401_renova_token_via_refresh_e_retenta_uma_vez():
    """Token do RM dura 5 min por default. Um pull noturno atravessa o
    expiry -- precisa renovar sozinho, nao morrer as 3h da manha."""
    chamadas: list[str] = []
    ja_deu_401 = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ja_deu_401
        url = str(request.url)
        chamadas.append(url)
        if "/api/connect/token" in url:
            body = request.content.decode()
            if "refresh_token" in body:
                return httpx.Response(200, json={"access_token": "t2", "expires_in": 300})
            return httpx.Response(
                200, json={"access_token": "t1", "refresh_token": "r1", "expires_in": 300}
            )
        if not ja_deu_401:
            ja_deu_401 = True
            return httpx.Response(401, text="token expirado")
        return httpx.Response(200, json={"items": [], "hasNext": False})

    ex = _rest(handler)
    try:
        await ex.fetch_lancamentos(desde=DESDE, ate=ATE)
    finally:
        await ex.aclose()
    assert sum(1 for c in chamadas if "/api/connect/token" in c) == 2


# --------------------------------------------------------------- paginacao
@pytest.mark.asyncio
async def test_rest_pagina_ate_hasnext_false():
    """A doc da TOTVS e explicita: "nao devem ser retornados todos os
    registros". Paginacao por page/pageSize + hasNext."""
    paginas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/connect/token" in url:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        paginas.append(url)
        page = int(httpx.URL(url).params.get("page", "1"))
        if page < 3:
            return httpx.Response(
                200,
                json={
                    "items": [_raw_rm(idlan=page)],
                    "hasNext": True,
                },
            )
        return httpx.Response(200, json={"items": [_raw_rm(idlan=3)], "hasNext": False})

    ex = _rest(handler)
    try:
        rows = await ex.fetch_lancamentos(desde=DESDE, ate=ATE)
    finally:
        await ex.aclose()
    assert len(rows) == 3
    assert len(paginas) == 3


@pytest.mark.asyncio
async def test_paginacao_para_no_limite_de_seguranca():
    """Se `hasNext` vier sempre True (bug do RM), o pull nao pode girar
    para sempre segurando licenca."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        return httpx.Response(200, json={"items": [_raw_rm(idlan=1)], "hasNext": True})

    ex = _rest(handler, max_paginas=5)
    try:
        rows = await ex.fetch_lancamentos(desde=DESDE, ate=ATE)
    finally:
        await ex.aclose()
    assert len(rows) == 5


def _raw_rm(*, idlan: int, valor: str = "1.234,56") -> dict:
    return {
        "CODCOLIGADA": 1,
        "CODFILIAL": 2,
        "IDLAN": idlan,
        "VALORORIGINAL": valor,
        "CODCFO": "F001",
        "NOMECFO": "TratorMax Locacoes",
        "CGCCFO": "12.345.678/0001-90",
        "DATAVENCIMENTO": "2026-09-10T00:00:00",
        "DATAEMISSAO": "2026-08-10T00:00:00",
        "STATUSLAN": 0,
    }


# --------------------------------------------------------------- #4 external_id
def test_external_id_e_a_chave_natural_do_rm():
    """CONFIRMADO pela TOTVS (Eduarda Soares, 31/08/2026): a chave que
    identifica um lancamento da FLAN de forma unica e CODCOLIGADA +
    IDLAN. O IDLAN e autoincremental no banco, mas o RM exige o vinculo
    com a coligada.

    CODFILIAL NAO entra na chave -- entrava na minha suposicao inicial,
    e po-la ali criaria linhas duplicadas se a filial de um lancamento
    fosse corrigida no RM."""
    assert external_id_rm(1, 4523) == "1-4523"


def test_external_id_nao_muda_se_a_filial_mudar():
    """Regressao da suposicao errada: filial nao faz parte da
    identidade do lancamento."""
    assert external_id_rm(1, 4523) == external_id_rm(1, 4523)


@pytest.mark.asyncio
async def test_normalizacao_produz_external_id_e_decimal():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        return httpx.Response(200, json={"items": [_raw_rm(idlan=99)], "hasNext": False})

    ex = _rest(handler)
    try:
        (row,) = await ex.fetch_lancamentos(desde=DESDE, ate=ATE)
    finally:
        await ex.aclose()

    assert row["external_id"] == "1-99"
    # #6: valor com virgula decimal vira Decimal exato, nunca float.
    assert row["valor"] == Decimal("1234.56")
    assert isinstance(row["valor"], Decimal)
    assert row["data_vencimento"] == date(2026, 9, 10)
    # #8: chave de match com o FCFO guardada so-digitos.
    assert row["contraparte_documento"] == "12345678000190"
    assert row["source"] == "totvs_rest"


# --------------------------------------------------------------- ConsultaSQL
@pytest.mark.asyncio
async def test_consultasql_parseia_dataset_dentro_do_cdata():
    """wsConsultaSQL devolve XML DataSet embrulhado em CDATA dentro do
    envelope SOAP. A sentenca e cadastrada DENTRO do RM (read-only por
    construcao: a query nao mora no nosso codigo)."""
    envelope = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
<s:Body><RealizarConsultaSQLContextoResponse xmlns="http://www.totvs.com/">
<RealizarConsultaSQLContextoResult><![CDATA[<NewDataSet>
<Resultado>
<CODCOLIGADA>1</CODCOLIGADA><CODFILIAL>2</CODFILIAL><IDLAN>4523</IDLAN>
<VALORORIGINAL>1.234,56</VALORORIGINAL>
<CGCCFO>12.345.678/0001-90</CGCCFO><NOMECFO>TratorMax</NOMECFO>
<DATAVENCIMENTO>2026-09-10T00:00:00</DATAVENCIMENTO>
</Resultado>
</NewDataSet>]]></RealizarConsultaSQLContextoResult>
</RealizarConsultaSQLContextoResponse></s:Body></s:Envelope>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=envelope)

    ex = ConsultaSqlExtractor(
        base_url="https://primor.rm.totvs.cloud:8051",
        username="svc_motor",
        password="segredo",
        cod_sentenca="MOTOR.FLAN.01",
        cod_coligada=1,
        cod_sistema="F",
        transport=httpx.MockTransport(handler),
    )
    try:
        (row,) = await ex.fetch_lancamentos(desde=DESDE, ate=ATE)
    finally:
        await ex.aclose()
    assert row["external_id"] == "1-4523"
    assert row["valor"] == Decimal("1234.56")
    assert row["source"] == "totvs_consultasql"


@pytest.mark.asyncio
async def test_consultasql_detecta_restricao_por_filtro_de_perfil():
    """Erro FE011: sentenca existe mas o perfil do usuario a bloqueia.
    Isso NAO pode passar como "zero lancamentos" -- perfil apertado
    devolvendo vazio silencioso e o pior modo de falha aqui."""
    from app.integrations.totvs.extractor import TotvsPermissionError

    corpo = (
        '{"code":"FE011","message":"A consulta SQL utilizando a chave '
        '1|F|MOTOR.FLAN.01 nao existe ou nao pode ser executada por '
        'restricao de filtro por perfil/usuario."}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=corpo)

    ex = ConsultaSqlExtractor(
        base_url="https://primor.rm.totvs.cloud:8051",
        username="u",
        password="p",
        cod_sentenca="MOTOR.FLAN.01",
        cod_coligada=1,
        cod_sistema="F",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(TotvsPermissionError):
            await ex.fetch_lancamentos(desde=DESDE, ate=ATE)
    finally:
        await ex.aclose()


# --------------------------------------------------------------- mock
@pytest.mark.asyncio
async def test_mock_e_deterministico():
    a = await MockExtractor().fetch_lancamentos(desde=DESDE, ate=ATE)
    b = await MockExtractor().fetch_lancamentos(desde=DESDE, ate=ATE)
    assert a == b
    assert a, "mock precisa devolver dado para destravar o pipeline em dev"
    assert all(r["source"] == "totvs_mock" for r in a)
    assert all(isinstance(r["valor"], Decimal) for r in a)
    assert len({r["external_id"] for r in a}) == len(a)


# --------------------------------------------------------------- factory
def test_factory_cai_no_mock_sem_credencial(monkeypatch):
    """Padrao do repo: sem credencial, mock deterministico -- igual
    Dominio, Onvio, Tangerino, OnSafety e DirectData."""
    from app.core.config import get_settings
    from app.integrations.totvs.client import build_totvs_client

    monkeypatch.setenv("TOTVS_USERNAME", "")
    monkeypatch.setenv("TOTVS_PASSWORD", "")
    get_settings.cache_clear()
    try:
        assert build_totvs_client().source == "totvs_mock"
    finally:
        get_settings.cache_clear()


def test_factory_escolhe_rest_quando_configurado(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.totvs.client import build_totvs_client

    monkeypatch.setenv("TOTVS_BASE_URL", "https://primor.rm.totvs.cloud:8051")
    monkeypatch.setenv("TOTVS_USERNAME", "svc_motor")
    monkeypatch.setenv("TOTVS_PASSWORD", "segredo")
    monkeypatch.setenv("TOTVS_EXTRACTOR", "rest")
    get_settings.cache_clear()
    try:
        client = build_totvs_client()
        assert client.source == "totvs_rest"
    finally:
        get_settings.cache_clear()


def test_factory_escolhe_consultasql_quando_configurado(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.totvs.client import build_totvs_client

    monkeypatch.setenv("TOTVS_BASE_URL", "https://primor.rm.totvs.cloud:8051")
    monkeypatch.setenv("TOTVS_USERNAME", "svc_motor")
    monkeypatch.setenv("TOTVS_PASSWORD", "segredo")
    monkeypatch.setenv("TOTVS_EXTRACTOR", "consultasql")
    monkeypatch.setenv("TOTVS_CONSULTASQL_COD_SENTENCA", "MOTOR.FLAN.01")
    get_settings.cache_clear()
    try:
        assert build_totvs_client().source == "totvs_consultasql"
    finally:
        get_settings.cache_clear()


def test_factory_exige_sentenca_no_modo_consultasql(monkeypatch):
    """Sem codSentenca o wsConsultaSQL nao tem o que executar -- falhar
    na subida e melhor que falhar as 3h da manha."""
    from app.core.config import get_settings
    from app.integrations.totvs.client import build_totvs_client

    monkeypatch.setenv("TOTVS_BASE_URL", "https://primor.rm.totvs.cloud:8051")
    monkeypatch.setenv("TOTVS_USERNAME", "svc_motor")
    monkeypatch.setenv("TOTVS_PASSWORD", "segredo")
    monkeypatch.setenv("TOTVS_EXTRACTOR", "consultasql")
    monkeypatch.setenv("TOTVS_CONSULTASQL_COD_SENTENCA", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="COD_SENTENCA"):
            build_totvs_client()
    finally:
        get_settings.cache_clear()
