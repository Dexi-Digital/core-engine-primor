"""Testes do adapter Infosimples (Modulo B.3)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from app.integrations.infosimples.client import (
    UFS_SUPORTADAS,
    InfosimplesClient,
    InfosimplesError,
    InfosimplesUFNaoSuportadaError,
    normalize_placa,
)

# --- helpers ---------------------------------------------------------------


def _real_client(handler):
    transport = MockTransport(handler)
    http = AsyncClient(transport=transport, base_url="https://api.infosimples.com")
    return InfosimplesClient(api_token="t-real", client=http)


# --- normalize_placa --------------------------------------------------------


def test_normalize_placa_remove_separadores():
    assert normalize_placa("ABC-1234") == "ABC1234"
    assert normalize_placa("abc 1A23") == "ABC1A23"
    assert normalize_placa("") == ""


# --- mock client (sem token) -----------------------------------------------


@pytest.mark.asyncio
async def test_mock_client_e_deterministico():
    c1 = InfosimplesClient(api_token=None)
    c2 = InfosimplesClient(api_token="")
    r1 = await c1.consultar_veiculo("ABC1234", "SP")
    r2 = await c2.consultar_veiculo("ABC1234", "SP")
    assert r1["source"] == "infosimples_mock"
    assert r1 == r2  # mesma placa+UF -> mesma resposta
    assert r1["placa"] == "ABC1234"
    assert r1["uf"] == "SP"


@pytest.mark.asyncio
async def test_mock_client_diverge_por_uf():
    c = InfosimplesClient(api_token=None)
    sp = await c.consultar_veiculo("ABC1234", "SP")
    mg = await c.consultar_veiculo("ABC1234", "MG")
    assert sp["uf"] == "SP" and mg["uf"] == "MG"
    # `marca_modelo` ou `cor` ou `n_multas` deve diferir entre estados
    diff = (
        sp.get("marca_modelo") != mg.get("marca_modelo")
        or sp.get("cor") != mg.get("cor")
        or len(sp.get("multas") or []) != len(mg.get("multas") or [])
    )
    assert diff, "mock deve produzir respostas distintas por UF"


@pytest.mark.asyncio
async def test_mock_client_ipva_e_licenciamento_presentes():
    c = InfosimplesClient(api_token=None)
    r = await c.consultar_veiculo("ABC1234", "SP")
    assert r["ipva"]["exercicio"] == 2025
    assert r["licenciamento"]["exercicio"] == 2025


# --- validacao de input -----------------------------------------------------


@pytest.mark.asyncio
async def test_uf_nao_suportada_levanta():
    c = InfosimplesClient(api_token=None)
    with pytest.raises(InfosimplesUFNaoSuportadaError):
        await c.consultar_veiculo("ABC1234", "RJ")


@pytest.mark.asyncio
async def test_placa_invalida_levanta_value_error():
    c = InfosimplesClient(api_token=None)
    with pytest.raises(ValueError):
        await c.consultar_veiculo("ABC", "SP")


def test_ufs_suportadas_constante():
    assert frozenset({"SP", "MG", "GO"}) == UFS_SUPORTADAS


# --- real client (com MockTransport simulando Infosimples) -----------------


@pytest.mark.asyncio
async def test_real_client_normaliza_payload():
    captured: dict = {}

    def handler(req: Request) -> Response:
        captured["url"] = str(req.url)
        return Response(
            200,
            json={
                "code": 200,
                "code_message": "OK",
                "data": [
                    {
                        "renavam": "12345678900",
                        "chassi": "9BW123ABC456D7890",
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
                        "restricoes": ["ALIENACAO FIDUCIARIA"],
                    }
                ],
            },
        )

    c = _real_client(handler)
    try:
        r = await c.consultar_veiculo("ABC1234", "SP")
    finally:
        await c.aclose()
    assert "consultas/detran/sp/veiculo" in captured["url"]
    assert r["source"] == "infosimples"
    assert r["placa"] == "ABC1234"
    assert r["uf"] == "SP"
    assert r["renavam"] == "12345678900"
    assert r["situacao"] == "REGULAR"
    assert r["ipva"]["pago"] is False
    assert len(r["multas"]) == 1
    assert r["raw"] is not None  # mantemos original


@pytest.mark.asyncio
async def test_real_client_eleva_em_status_nao_2xx():
    def handler(req: Request) -> Response:
        return Response(503, text="upstream offline")

    c = _real_client(handler)
    try:
        with pytest.raises(InfosimplesError, match="status 503"):
            await c.consultar_veiculo("ABC1234", "MG")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_real_client_eleva_em_code_nao_200():
    def handler(req: Request) -> Response:
        return Response(
            200,
            json={
                "code": 612,
                "code_message": "Site da Detran indisponivel",
                "data": [],
            },
        )

    c = _real_client(handler)
    try:
        with pytest.raises(InfosimplesError, match="code=612"):
            await c.consultar_veiculo("ABC1234", "GO")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_real_client_eleva_em_data_vazia():
    def handler(req: Request) -> Response:
        return Response(200, json={"code": 200, "data": []})

    c = _real_client(handler)
    try:
        with pytest.raises(InfosimplesError, match="sem dados"):
            await c.consultar_veiculo("ABC1234", "SP")
    finally:
        await c.aclose()


# --- CREA (D.6 fase 2, PR #28) ----------------------------------------------


from app.integrations.infosimples.client import (  # noqa: E402
    CREA_TIPOS_SUPORTADOS,
    InfosimplesCreaTipoNaoSuportadoError,
)


@pytest.mark.asyncio
async def test_crea_mock_art_e_deterministico():
    c = InfosimplesClient(api_token=None)
    r1 = await c.consultar_crea("MG", "art", "MG2023ABC123")
    r2 = await c.consultar_crea("MG", "art", "MG2023ABC123")
    assert r1["source"] == "infosimples_mock"
    assert r1 == r2
    assert r1["tipo"] == "art"
    assert r1["uf"] == "MG"
    assert r1["art"] is not None
    assert r1["art"]["numero"] == "MG2023ABC123"
    assert r1["art"]["situacao"] in {"ATIVA", "BAIXADA", "CANCELADA"}
    # tipo=art nao popula profissional/empresa.
    assert r1["profissional"] is None
    assert r1["empresa"] is None


@pytest.mark.asyncio
async def test_crea_mock_profissional_popula_profissional_so():
    c = InfosimplesClient(api_token=None)
    r = await c.consultar_crea("SP", "profissional", "SP-145678/D")
    assert r["tipo"] == "profissional"
    assert r["profissional"] is not None
    assert r["profissional"]["registro_crea"].startswith("SP-")
    assert r["profissional"]["situacao"] in {"REGULAR", "SUSPENSO", "CANCELADO"}
    assert r["art"] is None
    assert r["empresa"] is None


@pytest.mark.asyncio
async def test_crea_mock_empresa_popula_empresa_so():
    c = InfosimplesClient(api_token=None)
    r = await c.consultar_crea("GO", "empresa", "00000000000100")
    assert r["tipo"] == "empresa"
    assert r["empresa"] is not None
    assert r["empresa"]["cnpj"] == "00000000000100"
    assert r["empresa"]["arts_count"] >= 1
    assert r["art"] is None
    assert r["profissional"] is None


@pytest.mark.asyncio
async def test_crea_uf_nao_suportada_levanta():
    c = InfosimplesClient(api_token=None)
    with pytest.raises(InfosimplesUFNaoSuportadaError):
        await c.consultar_crea("RJ", "art", "RJ2023XYZ")


@pytest.mark.asyncio
async def test_crea_tipo_nao_suportado_levanta():
    c = InfosimplesClient(api_token=None)
    with pytest.raises(InfosimplesCreaTipoNaoSuportadoError):
        await c.consultar_crea("MG", "veiculo", "qualquer")


@pytest.mark.asyncio
async def test_crea_identificador_vazio_levanta():
    c = InfosimplesClient(api_token=None)
    with pytest.raises(ValueError, match="identificador vazio"):
        await c.consultar_crea("MG", "art", "  ")


@pytest.mark.asyncio
async def test_crea_identificador_muito_longo_levanta():
    c = InfosimplesClient(api_token=None)
    with pytest.raises(ValueError, match="identificador muito longo"):
        await c.consultar_crea("MG", "art", "x" * 65)


def test_crea_tipos_suportados_constante():
    assert {"art", "profissional", "empresa"} == CREA_TIPOS_SUPORTADOS


def test_normalize_crea_empresa_preserva_arts_count_zero():
    """Regressao identificada em review #28: empresa sem ART devolvia `arts_count=None`.

    `raw.get("arts_count") or raw.get("total_arts")` tratava 0 como falsy
    e caia no fallback. Pra empresa nova / sem ART registrada, isso
    perdia a info -- UI mostrava "—" em vez de "0 ARTs".
    """
    from app.integrations.infosimples.client import _normalize_crea_empresa  # noqa: PLC0415

    out = _normalize_crea_empresa({
        "cnpj": "00000000000100",
        "razao_social": "EMPRESA NOVA",
        "arts_count": 0,
    })
    assert out["arts_count"] == 0
    # Fallback ainda funciona quando `arts_count` ausente.
    out2 = _normalize_crea_empresa({"cnpj": "X", "total_arts": 5})
    assert out2["arts_count"] == 5
    # Quando ambos ausentes, devolve None.
    out3 = _normalize_crea_empresa({"cnpj": "X"})
    assert out3["arts_count"] is None


@pytest.mark.asyncio
async def test_crea_mock_art_dates_sao_sempre_validas():
    """Regressao identificada em review #28: mock devolvia day=31 em meses de 30 dias.

    Antes do fix, ~42% dos `idx` (107/256) geravam strings tipo '2023-02-31'
    que `_parse_iso_date` nao consegue parsear -> certidao importada com
    `validade=None`, perdendo expiration tracking.
    """
    from datetime import date as _date  # noqa: PLC0415 -- escopo de teste

    c = InfosimplesClient(api_token=None)
    # Cobre ~30 idx distintos. Mais que suficiente pra atravessar todos os
    # meses (idx % 12), incluindo os 5 com bug (Fev/Abr/Jun/Set/Nov).
    for i in range(40):
        ident = f"TEST{i:04d}"
        r = await c.consultar_crea("MG", "art", ident)
        for field in ("data_registro", "data_inicio", "data_termino_previsto"):
            value = r["art"][field]
            assert value, f"{field} vazio para {ident}"
            # Levanta ValueError se inválida -- exatamente o que `_parse_iso_date`
            # captura silenciosamente em prod.
            _date.fromisoformat(value)


@pytest.mark.asyncio
async def test_crea_real_client_normaliza_payload():
    captured: dict = {}

    def handler(req: Request) -> Response:
        captured["url"] = str(req.url)
        return Response(
            200,
            json={
                "code": 200,
                "code_message": "OK",
                "data": [
                    {
                        # Schema "achatado" -- alguns CREAs respondem assim
                        # ao endpoint /art (art_numero direto no top-level).
                        "art_numero": "MG2023XYZ999",
                        "tipo_obra": "CONSTRUCAO CIVIL",
                        "valor_total": "1500000.00",
                        "registrada_em": "2023-05-12",
                        "inicio": "2023-06-01",
                        "termino_previsto": "2024-12-31",
                        "situacao": "ATIVA",
                    }
                ],
            },
        )

    transport = MockTransport(handler)
    http = AsyncClient(
        transport=transport, base_url="https://api.infosimples.com"
    )
    c = InfosimplesClient(api_token="t-real", client=http)
    try:
        r = await c.consultar_crea("MG", "art", "MG2023XYZ999")
    finally:
        await c.aclose()
    assert "consultas/crea/mg/art" in captured["url"]
    assert r["source"] == "infosimples"
    assert r["art"]["numero"] == "MG2023XYZ999"
    assert r["art"]["tipo_servico"] == "CONSTRUCAO CIVIL"
    assert r["art"]["valor_contrato"] == "1500000.00"
    assert r["art"]["data_registro"] == "2023-05-12"
    assert r["art"]["situacao"] == "ATIVA"


@pytest.mark.asyncio
async def test_crea_real_client_eleva_em_status_nao_2xx():
    def handler(req: Request) -> Response:
        return Response(429, text="rate limit")

    c = _real_client(handler)
    try:
        with pytest.raises(InfosimplesError, match="status 429"):
            await c.consultar_crea("SP", "art", "SP2024X")
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_crea_real_client_eleva_em_code_nao_200():
    def handler(req: Request) -> Response:
        return Response(
            200,
            json={
                "code": 612,
                "code_message": "CREA-MG indisponivel",
                "data": [],
            },
        )

    c = _real_client(handler)
    try:
        with pytest.raises(InfosimplesError, match="code=612"):
            await c.consultar_crea("MG", "art", "MG2024X")
    finally:
        await c.aclose()
