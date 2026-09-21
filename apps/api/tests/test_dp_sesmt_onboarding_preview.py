"""Pre-visualizacao do push para a OnSafety -- ver as etapas SEM enviar.

Pedido de 21/09/2026: "pode ligar e so nao vamos clicar no botao que
vai chamar o cadastro real, mas precisamos ver as etapas". O token
disponivel e de PRODUCAO; a escrita continua atras do guard. A
pre-visualizacao mostra o que seria enviado, em que etapa o envio
pararia e por que -- sem tocar na OnSafety.

Tambem: o painel de admissao passa a dizer, por pessoa, se ja foi
refletida na OnSafety.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.onsafety import client as onsafety
from app.modules.dp_sesmt.router import get_onsafety_dep

_CPF_VALIDO = "52998224725"


async def _criar_employee(api_client: AsyncClient, auth_headers, **extra) -> int:
    resp = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": _CPF_VALIDO,
            "nome_completo": "JOSE DA SILVA",
            "cargo": "Operador de Escavadeira",
            "data_admissao": "2026-07-01",
            **extra,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _override(client: onsafety.OnsafetyClient):
    from app.main import app

    app.dependency_overrides[get_onsafety_dep] = lambda: client
    return app


# --- o corpo do envio e uma funcao pura ------------------------------------


def test_montar_body_e_puro_e_nao_envia() -> None:
    body = onsafety.montar_body_trabalhador(
        nome="JOSE DA SILVA", cpf="529.982.247-25", codigo_externo="7",
        projeto_id="est-1", matricula="M1", email=None,
        data_admissao="2026-07-01", data_nascimento=None,
    )
    assert body["cpf"] == "52998224725"
    assert body["codigoExterno"] == "7"
    assert body["projeto"] == {"id": "est-1"}
    assert body["dataAdmissao"] == "2026-07-01T00:00:00"
    assert "email" not in body and "dataNascimento" not in body


def test_montar_body_recusa_cpf_invalido() -> None:
    with pytest.raises(ValueError):
        onsafety.montar_body_trabalhador(
            nome="X", cpf="123", codigo_externo="1", projeto_id=None,
            matricula=None, email=None, data_admissao=None, data_nascimento=None,
        )


# --- GET /employees/{id}/sync-onsafety/preview ------------------------------


@pytest.mark.asyncio
async def test_preview_em_producao_com_guard_mostra_bloqueio_e_nao_envia(
    api_client: AsyncClient, auth_headers, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O caso real de hoje: token de producao, guard ligado."""
    emp_id = await _criar_employee(api_client, auth_headers, data_nascimento="1990-05-10")
    chamadas: list[str] = []

    class _Prod(onsafety.OnsafetyClient):
        def __init__(self) -> None:
            super().__init__(api_token="t-prod", base_url="https://api.onsafety.com.br",
                             allow_prod_write=False)

        async def create_or_update_trabalhador(self, **kw):
            chamadas.append("ENVIOU")
            raise AssertionError("preview nao pode enviar")

        async def aclose(self) -> None:
            pass

    app = _override(_Prod())
    monkeypatch.setenv("ONSAFETY_PROJETO_ID", "est-99")
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        r = await api_client.get(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety/preview",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
        get_settings.cache_clear()

    assert r.status_code == 200, r.text
    assert chamadas == []
    body = r.json()
    assert body["ambiente"] == "producao"
    assert body["pode_enviar"] is False
    assert body["payload"]["cpf"] == _CPF_VALIDO
    assert body["payload"]["projeto"] == {"id": "est-99"}
    etapas = {e["chave"]: e for e in body["etapas"]}
    assert etapas["dados"]["estado"] == "ok"
    assert etapas["estabelecimento"]["estado"] == "ok"
    assert etapas["guard"]["estado"] == "bloqueado"
    assert "ONSAFETY_ALLOW_PROD_WRITE" in etapas["guard"]["detalhe"]
    assert etapas["envio"]["estado"] == "pendente"


@pytest.mark.asyncio
async def test_preview_aponta_o_que_falta_nos_dados(
    api_client: AsyncClient, auth_headers, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem data de admissao a OnSafety aceita, mas o SST fica sem a base
    do ASO admissional -- o preview avisa antes de alguem clicar."""
    emp_id = await _criar_employee(api_client, auth_headers)
    # tira a data de admissao
    r = await api_client.put(
        f"/api/v1/dp-sesmt/employees/{emp_id}",
        json={"data_admissao": None}, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    app = _override(onsafety.OnsafetyClient(api_token=None))  # mock
    monkeypatch.delenv("ONSAFETY_PROJETO_ID", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        r = await api_client.get(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety/preview",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
        get_settings.cache_clear()
    body = r.json()
    etapas = {e["chave"]: e for e in body["etapas"]}
    assert etapas["dados"]["estado"] == "atencao"
    assert "admiss" in etapas["dados"]["detalhe"].lower()
    # sem ONSAFETY_PROJETO_ID a OnSafety recusa com 403 -- e bloqueio, nao aviso
    assert etapas["estabelecimento"]["estado"] == "bloqueado"
    assert body["pode_enviar"] is False


@pytest.mark.asyncio
async def test_preview_em_mock_diz_que_e_simulacao(
    api_client: AsyncClient, auth_headers, monkeypatch: pytest.MonkeyPatch,
) -> None:
    emp_id = await _criar_employee(api_client, auth_headers)
    app = _override(onsafety.OnsafetyClient(api_token=None))
    monkeypatch.setenv("ONSAFETY_PROJETO_ID", "est-1")
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        r = await api_client.get(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety/preview",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
        get_settings.cache_clear()
    assert r.json()["ambiente"] == "mock"
    assert r.json()["pode_enviar"] is True


@pytest.mark.asyncio
async def test_preview_traz_o_ultimo_envio(
    api_client: AsyncClient, auth_headers, monkeypatch: pytest.MonkeyPatch,
) -> None:
    emp_id = await _criar_employee(api_client, auth_headers)
    app = _override(onsafety.OnsafetyClient(api_token=None))
    try:
        r = await api_client.post(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety", headers=auth_headers,
        )
        assert r.status_code == 201
        r = await api_client.get(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety/preview",
            headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
    u = r.json()["ultimo_envio"]
    assert u is not None and u["status"] == "ok" and u["source"] == "onsafety_mock"


@pytest.mark.asyncio
async def test_preview_404_para_funcionario_inexistente(
    api_client: AsyncClient, auth_headers,
) -> None:
    r = await api_client.get(
        "/api/v1/dp-sesmt/employees/999999/sync-onsafety/preview", headers=auth_headers,
    )
    assert r.status_code == 404


# --- painel de admissao diz se a pessoa ja esta na OnSafety -----------------


@pytest.mark.asyncio
async def test_painel_de_admissao_mostra_reflexo_na_onsafety(
    api_client: AsyncClient, auth_headers, db_session: AsyncSession,
) -> None:
    emp_id = await _criar_employee(api_client, auth_headers)
    r = await api_client.post(
        "/api/v1/dp-sesmt/onboarding",
        json={"cpf": _CPF_VALIDO, "nome_completo": "—", "cargo": "—", "data_admissao": "—"},
        headers=auth_headers,
    )
    assert r.status_code in (200, 201), r.text

    painel = (await api_client.get("/api/v1/dp-sesmt/admissao/painel", headers=auth_headers)).json()
    item = next(i for i in painel["itens"] if i["employee_id"] == emp_id)
    assert item["onsafety"] == {"status": "nunca", "em": None, "erro": None}

    app = _override(onsafety.OnsafetyClient(api_token=None))
    try:
        await api_client.post(
            f"/api/v1/dp-sesmt/employees/{emp_id}/sync-onsafety", headers=auth_headers,
        )
    finally:
        app.dependency_overrides.pop(get_onsafety_dep, None)
    painel = (await api_client.get("/api/v1/dp-sesmt/admissao/painel", headers=auth_headers)).json()
    item = next(i for i in painel["itens"] if i["employee_id"] == emp_id)
    assert item["onsafety"]["status"] == "ok"
    assert item["onsafety"]["em"] is not None
