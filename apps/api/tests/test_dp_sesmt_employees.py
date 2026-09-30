"""CRUD de funcionarios + dossie endpoints (Modulo A)."""

from __future__ import annotations

import json

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.integrations.brasilapi.client import BrasilAPIClient
from app.integrations.directdata.client import DirectDataClient
from app.integrations.viacep.client import ViaCEPClient
from app.main import app
from app.modules.dp_sesmt.models import DossieConsultaLog
from app.modules.dp_sesmt.router import (
    _get_brasilapi,
    _get_directdata,
    _get_viacep,
)

VALID_CPF_1 = "11144477735"
VALID_CPF_2 = "39053344705"


# --- CRUD --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_employee_persiste_e_normaliza_cpf(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": "111.444.777-35",
            "nome_completo": "Joao da Silva",
            "cargo": "Pedreiro",
            "obra": "Obra Centro",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # CPF deve ser persistido sem mascara.
    assert body["cpf"] == VALID_CPF_1
    assert body["status"] == "ativo"
    assert body["source"] == "manual"


@pytest.mark.asyncio
async def test_create_employee_aceita_e_retorna_flags_sst(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Regressao: flags SST (D1) precisam estar nos schemas Pydantic.

    Sem isso, as regras condicionais do diagnostico documental
    (NR-12 se is_operador_maquina, NR-35 se is_alturas, toxicologico
    se is_motorista, NR-10 se is_eletricista, NR-18 default exceto
    is_admin_office) nunca disparam porque os campos sao silenciosamente
    descartados pelo Pydantic em POST/PUT/GET.
    """
    create = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": VALID_CPF_1,
            "nome_completo": "Motorista Operador",
            "cargo": "Motorista",
            "is_motorista": True,
            "is_operador_maquina": True,
        },
        headers=auth_headers,
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["is_motorista"] is True
    assert body["is_operador_maquina"] is True
    assert body["is_admin_office"] is False
    assert body["is_alturas"] is False
    assert body["is_eletricista"] is False

    # GET tem que devolver os mesmos valores.
    fetched = await api_client.get(f"/api/v1/dp-sesmt/employees/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["is_motorista"] is True
    assert fetched.json()["is_operador_maquina"] is True

    # PATCH tem que conseguir atualizar.
    patched = await api_client.put(
        f"/api/v1/dp-sesmt/employees/{body['id']}",
        json={"is_motorista": False, "is_alturas": True},
        headers=auth_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["is_motorista"] is False
    assert patched.json()["is_alturas"] is True
    # Flags nao mexidas precisam continuar como estavam.
    assert patched.json()["is_operador_maquina"] is True


@pytest.mark.asyncio
async def test_create_employee_rejeita_cpf_invalido(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": "12345678901",
            "nome_completo": "Quem Quer",
            "cargo": "Pedreiro",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "CPF" in resp.text or "cpf" in resp.text


@pytest.mark.asyncio
async def test_create_employee_409_em_cpf_duplicado(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    payload = {
        "cpf": VALID_CPF_1,
        "nome_completo": "Joao da Silva",
        "cargo": "Pedreiro",
    }
    r1 = await api_client.post("/api/v1/dp-sesmt/employees", json=payload, headers=auth_headers)
    assert r1.status_code == 201
    r2 = await api_client.post("/api/v1/dp-sesmt/employees", json=payload, headers=auth_headers)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_list_employees_filtros_e_busca(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # Cria 2 funcionarios distintos.
    await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": VALID_CPF_1,
            "nome_completo": "Joao da Silva",
            "cargo": "Pedreiro",
            "obra": "Obra A",
        },
        headers=auth_headers,
    )
    await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": VALID_CPF_2,
            "nome_completo": "Maria Souza",
            "cargo": "Engenheira",
            "obra": "Obra B",
            "status": "afastado",
        },
        headers=auth_headers,
    )

    r = await api_client.get("/api/v1/dp-sesmt/employees")
    assert r.status_code == 200
    assert r.json()["total"] == 2

    r = await api_client.get("/api/v1/dp-sesmt/employees", params={"obra": "Obra A"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["nome_completo"] == "Joao da Silva"

    r = await api_client.get("/api/v1/dp-sesmt/employees", params={"status": "afastado"})
    assert r.json()["total"] == 1

    r = await api_client.get("/api/v1/dp-sesmt/employees", params={"search": "Maria"})
    assert r.json()["total"] == 1

    # Busca por CPF com mascara deve normalizar.
    r = await api_client.get("/api/v1/dp-sesmt/employees", params={"search": "111.444"})
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_employees_filtra_por_setor_e_admin_office(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Cobre os filtros novos usados pela tela `/rh/equipe-administrativa`."""
    # Admin (Licitacoes) + admin (TI) + operacional sem setor.
    await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": VALID_CPF_1,
            "nome_completo": "Brenda Adm",
            "cargo": "Analista de Licitacoes",
            "setor": "Licitacoes",
            "is_admin_office": True,
        },
        headers=auth_headers,
    )
    await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": VALID_CPF_2,
            "nome_completo": "Wanderson Adm",
            "cargo": "Coordenador de TI",
            "setor": "TI",
            "is_admin_office": True,
        },
        headers=auth_headers,
    )
    await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": "52998224725",  # CPF sintaticamente valido
            "nome_completo": "Pedreiro Operacional",
            "cargo": "Pedreiro",
            "obra": "Obra A",
        },
        headers=auth_headers,
    )

    # is_admin_office=true devolve so os dois administrativos.
    r = await api_client.get(
        "/api/v1/dp-sesmt/employees", params={"is_admin_office": "true"}
    )
    assert r.json()["total"] == 2
    nomes = {e["nome_completo"] for e in r.json()["items"]}
    assert nomes == {"Brenda Adm", "Wanderson Adm"}

    # is_admin_office=false devolve so o operacional.
    r = await api_client.get(
        "/api/v1/dp-sesmt/employees", params={"is_admin_office": "false"}
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["nome_completo"] == "Pedreiro Operacional"

    # Filtro por setor (substring ilike): "Licit" casa "Licitacoes".
    r = await api_client.get(
        "/api/v1/dp-sesmt/employees", params={"setor": "Licit"}
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["setor"] == "Licitacoes"

    # Combinacao de filtros: admin + setor=TI.
    r = await api_client.get(
        "/api/v1/dp-sesmt/employees",
        params={"is_admin_office": "true", "setor": "TI"},
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["nome_completo"] == "Wanderson Adm"


@pytest.mark.asyncio
async def test_update_employee_partial(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": VALID_CPF_1,
            "nome_completo": "Joao da Silva",
            "cargo": "Pedreiro",
        },
        headers=auth_headers,
    )
    employee_id = r.json()["id"]
    r = await api_client.put(
        f"/api/v1/dp-sesmt/employees/{employee_id}",
        json={"status": "afastado", "observacoes": "INSS"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "afastado"
    assert body["observacoes"] == "INSS"
    assert body["nome_completo"] == "Joao da Silva"  # preservado


@pytest.mark.asyncio
async def test_create_with_empregos_anteriores(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": VALID_CPF_1,
            "nome_completo": "Joao da Silva",
            "cargo": "Pedreiro",
            "empregos_anteriores": [
                {
                    "empresa_cnpj": "00000000000191",
                    "empresa_razao_social": "Banco do Brasil",
                    "cargo": "Estagiario",
                    "inicio": "2020-01-01",
                    "fim": "2021-06-01",
                }
            ],
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["empregos_anteriores"]) == 1
    assert body["empregos_anteriores"][0]["empresa_cnpj"] == "00000000000191"


@pytest.mark.asyncio
async def test_delete_employee(api_client: AsyncClient, auth_headers: dict[str, str]) -> None:
    r = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={
            "cpf": VALID_CPF_1,
            "nome_completo": "Joao",
            "cargo": "Pedreiro",
        },
        headers=auth_headers,
    )
    employee_id = r.json()["id"]
    r = await api_client.delete(f"/api/v1/dp-sesmt/employees/{employee_id}", headers=auth_headers)
    assert r.status_code == 204
    r = await api_client.get(f"/api/v1/dp-sesmt/employees/{employee_id}")
    assert r.status_code == 404


# --- Dossie endpoints --------------------------------------------------------


def _override_viacep(handler):
    def _factory() -> ViaCEPClient:
        transport = httpx.MockTransport(handler)
        http = httpx.AsyncClient(base_url="https://viacep.com.br", transport=transport)
        return ViaCEPClient(client=http)

    return _factory


def _override_brasilapi(handler):
    def _factory() -> BrasilAPIClient:
        transport = httpx.MockTransport(handler)
        http = httpx.AsyncClient(base_url="https://brasilapi.com.br", transport=transport)
        return BrasilAPIClient(client=http)

    return _factory


def _override_directdata():
    def _factory() -> DirectDataClient:
        return DirectDataClient(api_key=None)  # forca mock

    return _factory


@pytest.mark.asyncio
async def test_dossie_cep_ok(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "cep": "01310-100",
                "logradouro": "Avenida Paulista",
                "bairro": "Bela Vista",
                "localidade": "Sao Paulo",
                "uf": "SP",
            },
        )

    app.dependency_overrides[_get_viacep] = _override_viacep(handler)
    try:
        r = await api_client.get(
            "/api/v1/dp-sesmt/dossie/cep/01310100", headers=auth_headers
        )
        assert r.status_code == 200
        body = r.json()
        assert body["cidade"] == "Sao Paulo"
        assert body["uf"] == "SP"
    finally:
        app.dependency_overrides.pop(_get_viacep, None)

    # Log de auditoria foi gravado.
    from sqlalchemy import select

    rows = (await db_session.execute(select(DossieConsultaLog))).scalars().all()
    assert any(r.fonte == "viacep" and r.sucesso for r in rows)


@pytest.mark.asyncio
async def test_dossie_cep_404(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"erro": True})

    app.dependency_overrides[_get_viacep] = _override_viacep(handler)
    try:
        r = await api_client.get(
            "/api/v1/dp-sesmt/dossie/cep/99999999", headers=auth_headers
        )
        assert r.status_code == 404
    finally:
        app.dependency_overrides.pop(_get_viacep, None)


@pytest.mark.asyncio
async def test_dossie_cnpj_ok(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "cnpj": "00000000000191",
                "razao_social": "Banco do Brasil",
                "uf": "DF",
            },
        )

    app.dependency_overrides[_get_brasilapi] = _override_brasilapi(handler)
    try:
        r = await api_client.get(
            "/api/v1/dp-sesmt/dossie/cnpj/00000000000191", headers=auth_headers
        )
        assert r.status_code == 200
        assert r.json()["razao_social"] == "Banco do Brasil"
    finally:
        app.dependency_overrides.pop(_get_brasilapi, None)

    # CNPJ e publico: sem justificativa, mas o actor vai para o audit.
    audits = await _audits_dossie(db_session)
    assert len(audits) == 1
    assert audits[0].action == "consulta_cnpj"
    assert audits[0].actor == _ADMIN_EMAIL
    meta = json.loads(audits[0].metadata_json)
    assert meta["cnpj"] == "00000000000191"
    assert "justificativa" not in meta


@pytest.mark.asyncio
async def test_dossie_cpf_modo_mock(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    app.dependency_overrides[_get_directdata] = _override_directdata()
    try:
        r = await api_client.get(
            f"/api/v1/dp-sesmt/dossie/cpf/{VALID_CPF_1}",
            params={"justificativa": _JUSTIFICATIVA},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "directdata_mock"
        assert body["nome"]
    finally:
        app.dependency_overrides.pop(_get_directdata, None)


@pytest.mark.asyncio
async def test_dossie_cpf_invalido_400(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    app.dependency_overrides[_get_directdata] = _override_directdata()
    try:
        r = await api_client.get(
            "/api/v1/dp-sesmt/dossie/cpf/12345678901",
            params={"justificativa": _JUSTIFICATIVA},
            headers=auth_headers,
        )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(_get_directdata, None)


# --- LGPD: audit + justificativa do dossie ----------------------------------

_JUSTIFICATIVA = "Conferencia de dados na admissao do funcionario"


# Mesmo admin criado pela fixture `auth_headers` (conftest).
_ADMIN_EMAIL = "test-admin@primor.com"


async def _audits_dossie(db: AsyncSession) -> list[AuditLog]:
    from sqlalchemy import select

    return list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.resource == "dp_sesmt.dossie")
                .order_by(AuditLog.id)
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_dossie_exige_auth(api_client: AsyncClient) -> None:
    for path in (
        "/api/v1/dp-sesmt/dossie/cep/01310100",
        "/api/v1/dp-sesmt/dossie/cnpj/00000000000191",
        f"/api/v1/dp-sesmt/dossie/cpf/{VALID_CPF_1}?justificativa=x",
    ):
        r = await api_client.get(path)
        assert r.status_code == 401, path


@pytest.mark.asyncio
@pytest.mark.parametrize("justificativa", [None, "", "          ", "curta"])
async def test_dossie_cpf_sem_justificativa_422(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
    justificativa: str | None,
) -> None:
    app.dependency_overrides[_get_directdata] = _override_directdata()
    try:
        params = {} if justificativa is None else {"justificativa": justificativa}
        r = await api_client.get(
            f"/api/v1/dp-sesmt/dossie/cpf/{VALID_CPF_1}",
            params=params,
            headers=auth_headers,
        )
        assert r.status_code == 422, r.text
    finally:
        app.dependency_overrides.pop(_get_directdata, None)
    # Consulta nem chegou ao provedor: nada logado.
    assert await _audits_dossie(db_session) == []


@pytest.mark.asyncio
async def test_dossie_cpf_grava_audit_com_actor_justificativa_e_cpf_mascarado(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    app.dependency_overrides[_get_directdata] = _override_directdata()
    try:
        r = await api_client.get(
            f"/api/v1/dp-sesmt/dossie/cpf/{VALID_CPF_1}",
            params={"justificativa": f"  {_JUSTIFICATIVA}  ", "employee_id": 42},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.pop(_get_directdata, None)

    audits = await _audits_dossie(db_session)
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == "consulta_cpf"
    assert audit.actor == _ADMIN_EMAIL
    assert audit.resource_id == "42"
    meta = json.loads(audit.metadata_json)
    assert meta["justificativa"] == _JUSTIFICATIVA
    assert meta["cpf"] == "***.444.777-**"
    assert meta["sucesso"] is True
    assert meta["employee_id"] == 42
    # CPF nunca em claro no audit (nem formatado).
    assert VALID_CPF_1 not in audit.metadata_json
    assert "111.444.777-35" not in audit.metadata_json


@pytest.mark.asyncio
async def test_dossie_cpf_invalido_tambem_e_auditado(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    app.dependency_overrides[_get_directdata] = _override_directdata()
    try:
        r = await api_client.get(
            "/api/v1/dp-sesmt/dossie/cpf/12345678901",
            params={"justificativa": _JUSTIFICATIVA},
            headers=auth_headers,
        )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(_get_directdata, None)
    audits = await _audits_dossie(db_session)
    assert len(audits) == 1
    meta = json.loads(audits[0].metadata_json)
    assert meta["sucesso"] is False
    assert meta["cpf"] == "***.456.789-**"
    assert "12345678901" not in audits[0].metadata_json


@pytest.mark.asyncio
async def test_dossie_cep_audita_actor_e_justificativa_opcional(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"cep": "01310-100", "localidade": "Sao Paulo", "uf": "SP"}
        )

    app.dependency_overrides[_get_viacep] = _override_viacep(handler)
    try:
        r1 = await api_client.get(
            "/api/v1/dp-sesmt/dossie/cep/01310100", headers=auth_headers
        )
        r2 = await api_client.get(
            "/api/v1/dp-sesmt/dossie/cep/01310100",
            params={"justificativa": "Endereco para ficha de registro"},
            headers=auth_headers,
        )
        assert r1.status_code == 200 and r2.status_code == 200
    finally:
        app.dependency_overrides.pop(_get_viacep, None)

    audits = await _audits_dossie(db_session)
    assert [a.action for a in audits] == ["consulta_cep", "consulta_cep"]
    assert all(a.actor == _ADMIN_EMAIL for a in audits)
    m1, m2 = (json.loads(a.metadata_json) for a in audits)
    assert m1["cep"] == "01310100" and "justificativa" not in m1
    assert m2["justificativa"] == "Endereco para ficha de registro"


def test_mask_cpf() -> None:
    from app.core.cpf import mask_cpf

    assert mask_cpf("111.444.777-35") == "***.444.777-**"
    assert mask_cpf("123") == "***"
    assert mask_cpf("") == "***"


# --- Regressões de bugs flagados em review ----------------------------------


@pytest.mark.asyncio
async def test_update_employee_rejeita_status_arbitrario(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Review #10: EmployeeUpdate aceitava qualquer string de
    status porque herdava de BaseModel direto, sem o validator.
    """
    r = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={"cpf": VALID_CPF_1, "nome_completo": "Joao", "cargo": "Pedreiro"},
        headers=auth_headers,
    )
    employee_id = r.json()["id"]
    bad = await api_client.put(
        f"/api/v1/dp-sesmt/employees/{employee_id}",
        json={"status": "fantasma"},
        headers=auth_headers,
    )
    assert bad.status_code == 422
    # Garante que o status ficou intocado.
    r = await api_client.get(f"/api/v1/dp-sesmt/employees/{employee_id}")
    assert r.json()["status"] == "ativo"


@pytest.mark.asyncio
async def test_create_update_delete_geram_audit_log(
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    """AGENTS.md: mutacoes em recurso sensivel devem gravar em audit_log."""
    from sqlalchemy import select

    from app.audit.models import AuditLog

    r = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={"cpf": VALID_CPF_1, "nome_completo": "Joao", "cargo": "Pedreiro"},
        headers=auth_headers,
    )
    employee_id = r.json()["id"]
    await api_client.put(
        f"/api/v1/dp-sesmt/employees/{employee_id}",
        json={"status": "afastado"},
        headers=auth_headers,
    )
    await api_client.delete(f"/api/v1/dp-sesmt/employees/{employee_id}", headers=auth_headers)

    rows = (await db_session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    employee_rows = [r for r in rows if r.resource == "dp_sesmt.employee"]
    actions = [r.action for r in employee_rows]
    assert actions == ["create", "update", "delete"]
    # Resource_id correto e metadata gravados.
    for r in employee_rows:
        assert r.resource_id == str(employee_id)
        assert r.metadata_json  # JSON serializado nao-vazio
        # Regressao PR #20: actor agora vem do JWT, nao mais hardcoded "system".
        assert r.actor == "test-admin@primor.com"


@pytest.mark.asyncio
async def test_update_sem_mudanca_real_nao_polui_audit(
    api_client: AsyncClient, db_session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    """Update sem alteracao efetiva nao deve gerar AuditLog (evita ruido)."""
    from sqlalchemy import select

    from app.audit.models import AuditLog

    r = await api_client.post(
        "/api/v1/dp-sesmt/employees",
        json={"cpf": VALID_CPF_1, "nome_completo": "Joao", "cargo": "Pedreiro"},
        headers=auth_headers,
    )
    employee_id = r.json()["id"]
    # Send same status -- nada deve mudar.
    await api_client.put(
        f"/api/v1/dp-sesmt/employees/{employee_id}",
        json={"status": "ativo"},
        headers=auth_headers,
    )
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    update_rows = [r for r in rows if r.action == "update" and r.resource == "dp_sesmt.employee"]
    assert update_rows == []
