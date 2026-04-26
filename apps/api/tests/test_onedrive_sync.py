"""Tests pro sync OneDrive -> tabelas de documentos (D1 fase 2)."""
from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.integrations.onedrive.client import OneDriveClient, OneDriveMockClient
from app.modules.dp_sesmt.models import Employee, EmployeeDocument
from app.modules.licitacoes.models import EmpresaDocumento
from app.modules.manutencao_frota.models import DocumentoVeiculo, Veiculo
from app.modules.obras.models import Obra, ObraDocumento
from app.modules.onedrive_sync.models import (
    RUN_DONE,
    SCOPE_ALL,
    SCOPE_DP,
    SOURCE_ONEDRIVE_SYNC,
)
from app.modules.onedrive_sync.parser import parse_path
from app.modules.onedrive_sync.service import run_sync

# --------------------------- parser ----------------------------------------


def test_parse_path_dp_valido() -> None:
    m = parse_path("dp/42/NR12.pdf")
    assert m is not None
    assert m.area == "dp"
    assert m.entity_key == "42"
    assert m.doc_tipo == "NR12"


def test_parse_path_frota_lower_normalizado() -> None:
    m = parse_path("frota/ABC1D23/crlv.pdf")
    assert m is not None
    assert m.area == "frota"
    assert m.entity_key == "ABC1D23"
    assert m.doc_tipo == "CRLV"


def test_parse_path_empresa_sem_entity_key() -> None:
    m = parse_path("empresa/SICAF.pdf")
    assert m is not None
    assert m.area == "empresa"
    assert m.entity_key is None
    assert m.doc_tipo == "SICAF"


def test_parse_path_rejeita_area_invalida() -> None:
    assert parse_path("foo/bar.pdf") is None
    assert parse_path("lixo/123/nada.pdf") is None


def test_parse_path_rejeita_shape_errado() -> None:
    # empresa espera 2 niveis (area/file), nao 3
    assert parse_path("empresa/123/SICAF.pdf") is None
    # dp espera 3 niveis
    assert parse_path("dp/NR12.pdf") is None


def test_parse_path_filename_com_espaco() -> None:
    m = parse_path("empresa/Contrato Social.pdf")
    assert m is not None
    assert m.doc_tipo == "CONTRATO_SOCIAL"


# --------------------------- OneDriveMockClient.list_folder ----------------


@pytest.mark.asyncio
async def test_mock_client_list_folder_recursive() -> None:
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path="dp/1/NR12.pdf")
    client.seed(relative_path="dp/2/NR18.pdf")
    client.seed(relative_path="empresa/SICAF.pdf")
    items = await client.list_folder()
    paths = {it["path"] for it in items}
    assert paths == {"dp/1/NR12.pdf", "dp/2/NR18.pdf", "empresa/SICAF.pdf"}


@pytest.mark.asyncio
async def test_mock_client_list_folder_prefixado() -> None:
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path="dp/1/NR12.pdf")
    client.seed(relative_path="empresa/SICAF.pdf")
    items = await client.list_folder(relative_path="dp")
    paths = {it["path"] for it in items}
    assert paths == {"dp/1/NR12.pdf"}


@pytest.mark.asyncio
async def test_mock_client_list_folder_nao_recursive() -> None:
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path="SICAF.pdf")
    client.seed(relative_path="dp/1/NR12.pdf")
    items = await client.list_folder(recursive=False)
    paths = {it["path"] for it in items}
    assert paths == {"SICAF.pdf"}


# --------------------------- real OneDriveClient.list_folder --------------


@pytest.mark.asyncio
async def test_real_client_list_folder_strips_root_prefix() -> None:
    """Regressao: path devolvido deve ser relativo ao `root_folder`.

    O Graph API devolve paths absolutos a partir da raiz do drive
    (`MotorCentral/editais/dp/42/NR12.pdf`). O parser de sync espera
    paths relativos (`dp/42/NR12.pdf`). Sem o strip do prefixo, o sync
    falharia em producao (`MotorCentral` nao e area valida) enquanto o
    mock passa -- bug silencioso.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(
                200, json={"access_token": "tok", "expires_in": 3600}
            )
        # children da raiz MotorCentral/editais
        if "/root:/MotorCentral/editais:/children" in str(req.url):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "F1",
                            "name": "dp",
                            "folder": {"childCount": 1},
                        },
                        {
                            "id": "I1",
                            "name": "SICAF.pdf",
                            "size": 1024,
                            "lastModifiedDateTime": "2026-01-01T00:00:00Z",
                        },
                    ]
                },
            )
        # children da subpasta dp
        if "/root:/MotorCentral/editais/dp:/children" in str(req.url):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "I2",
                            "name": "NR12.pdf",
                            "size": 2048,
                            "lastModifiedDateTime": "2026-02-01T00:00:00Z",
                            # Graph manda `parentReference.path` mas o nosso
                            # cliente nao le isso -- constroi path ele mesmo.
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = OneDriveClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        drive_id="d",
        root_folder="MotorCentral/editais",
        transport=httpx.MockTransport(handler),
    )
    try:
        items = await client.list_folder()
    finally:
        await client.aclose()

    paths = {it["path"] for it in items}
    assert paths == {"SICAF.pdf", "dp/NR12.pdf"}


# --------------------------- service.run_sync ------------------------------


async def _seed_entities(db: AsyncSession) -> tuple[Employee, Veiculo, Obra]:
    emp = Employee(
        cpf="111.111.111-11",
        nome_completo="Joao Operario",
        cargo="Pedreiro",
        status="ativo",
        is_operador_maquina=True,
    )
    db.add(emp)
    veic = Veiculo(placa="ABC1D23", marca="Ford", modelo="Cargo", status="ativo")
    db.add(veic)
    obra = Obra(codigo="OBR-001", nome="Sede", status="ativa")
    db.add(obra)
    await db.commit()
    await db.refresh(emp)
    await db.refresh(veic)
    await db.refresh(obra)
    return emp, veic, obra


@pytest.mark.asyncio
async def test_run_sync_cria_docs_nas_4_areas(db_session: AsyncSession) -> None:
    emp, veic, obra = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path=f"dp/{emp.id}/NR12.pdf")
    client.seed(relative_path=f"frota/{veic.placa}/CRLV.pdf")
    client.seed(relative_path=f"obras/{obra.codigo}/ART.pdf")
    client.seed(relative_path="empresa/SICAF.pdf")

    run = await run_sync(
        db_session, client=client, scope=SCOPE_ALL, actor="operator@primor.com"
    )
    assert run.status == RUN_DONE
    assert run.files_scanned == 4
    assert run.docs_created == 4
    assert run.docs_updated == 0
    assert run.errors_count == 0

    # Verifica que os docs foram persistidos com as colunas do sync.
    emp_doc = (
        await db_session.execute(
            select(EmployeeDocument).where(EmployeeDocument.employee_id == emp.id)
        )
    ).scalars().first()
    assert emp_doc is not None
    assert emp_doc.tipo == "NR12"
    assert emp_doc.source == SOURCE_ONEDRIVE_SYNC
    assert emp_doc.onedrive_item_id is not None
    assert emp_doc.onedrive_sync_run_id == run.id

    veic_doc = (
        await db_session.execute(
            select(DocumentoVeiculo).where(DocumentoVeiculo.veiculo_id == veic.id)
        )
    ).scalars().first()
    assert veic_doc is not None
    assert veic_doc.tipo == "crlv"  # frota usa lower na convencao

    obra_doc = (
        await db_session.execute(
            select(ObraDocumento).where(ObraDocumento.obra_id == obra.id)
        )
    ).scalars().first()
    assert obra_doc is not None
    assert obra_doc.tipo == "ART"

    emp_doc_empresa = (
        await db_session.execute(select(EmpresaDocumento))
    ).scalars().first()
    assert emp_doc_empresa is not None
    assert emp_doc_empresa.tipo == "SICAF"


@pytest.mark.asyncio
async def test_run_sync_idempotente(db_session: AsyncSession) -> None:
    """Segundo run nao deve duplicar docs nem criar novos."""
    emp, _, _ = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path=f"dp/{emp.id}/NR12.pdf")

    run1 = await run_sync(db_session, client=client, actor="a@b.com")
    assert run1.docs_created == 1
    assert run1.docs_updated == 0

    # Mesmo run de novo: last_modified inalterado => skip.
    run2 = await run_sync(db_session, client=client, actor="a@b.com")
    assert run2.docs_created == 0
    assert run2.docs_updated == 0
    assert run2.docs_skipped >= 1

    # So tem 1 row no banco.
    docs = (await db_session.execute(select(EmployeeDocument))).scalars().all()
    assert len(docs) == 1


@pytest.mark.asyncio
async def test_run_sync_update_quando_last_modified_muda(
    db_session: AsyncSession,
) -> None:
    emp, _, _ = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(
        relative_path=f"dp/{emp.id}/NR12.pdf",
        last_modified="2026-01-01T00:00:00Z",
    )
    await run_sync(db_session, client=client, actor="a@b.com")

    # Re-seed com last_modified diferente (simula upload de nova versao).
    client.seed(
        relative_path=f"dp/{emp.id}/NR12.pdf",
        last_modified="2026-02-01T00:00:00Z",
    )
    run2 = await run_sync(db_session, client=client, actor="a@b.com")
    assert run2.docs_created == 0
    assert run2.docs_updated == 1


@pytest.mark.asyncio
async def test_run_sync_ignora_path_invalido(db_session: AsyncSession) -> None:
    emp, _, _ = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path=f"dp/{emp.id}/NR12.pdf")
    client.seed(relative_path="lixo/arquivo-solto.pdf")
    client.seed(relative_path="README.txt")

    run = await run_sync(db_session, client=client, actor="a@b.com")
    assert run.files_scanned == 3
    assert run.docs_created == 1
    assert run.docs_skipped == 2


@pytest.mark.asyncio
async def test_run_sync_error_recovery_apos_db_commit_falhar(
    db_session: AsyncSession,
) -> None:
    """Regressao: se db.commit() dentro do try falhar, o handler de
    erro precisa fazer rollback antes do proprio commit -- caso
    contrario SQLAlchemy levanta PendingRollbackError e o run fica
    preso em `running` com o router devolvendo 500 opaco.
    """
    from unittest.mock import patch

    from sqlalchemy.exc import OperationalError

    from app.modules.onedrive_sync.models import RUN_ERROR

    emp, _, _ = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path=f"dp/{emp.id}/NR12.pdf")

    original_commit = db_session.commit
    calls = {"n": 0}

    async def flaky_commit() -> None:
        calls["n"] += 1
        # Primeiro commit e o que persiste o run em `running` (OK).
        # Segundo e o que finaliza o loop de sync -- forcamos falha
        # pra exercitar o recovery.
        if calls["n"] == 2:
            raise OperationalError("boom", params=None, orig=Exception("boom"))
        await original_commit()

    with patch.object(db_session, "commit", flaky_commit):
        run = await run_sync(db_session, client=client, actor="a@b.com")

    assert run.status == RUN_ERROR
    assert run.error_message is not None
    assert "boom" in run.error_message.lower() or "operational" in run.error_message.lower()
    # O registro persistiu mesmo apos o commit interno falhar.
    assert run.id is not None


@pytest.mark.asyncio
async def test_run_sync_erro_quando_entidade_nao_existe(
    db_session: AsyncSession,
) -> None:
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path="dp/9999/NR12.pdf")

    run = await run_sync(db_session, client=client, actor="a@b.com")
    assert run.status == RUN_DONE  # erro por arquivo nao aborta o run
    assert run.errors_count == 1
    assert run.docs_created == 0
    assert run.summary_json is not None
    errors = run.summary_json.get("errors", [])
    assert errors and "funcionario" in errors[0]["reason"].lower()


@pytest.mark.asyncio
async def test_run_sync_respeita_scope(db_session: AsyncSession) -> None:
    emp, veic, _ = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path=f"dp/{emp.id}/NR12.pdf")
    client.seed(relative_path=f"frota/{veic.placa}/CRLV.pdf")

    run = await run_sync(db_session, client=client, scope=SCOPE_DP)
    assert run.docs_created == 1
    assert run.docs_skipped == 1  # frota foi skipada por scope


@pytest.mark.asyncio
async def test_run_sync_grava_audit_log(db_session: AsyncSession) -> None:
    emp, _, _ = await _seed_entities(db_session)
    client = OneDriveMockClient(root_folder="MotorCentral/editais")
    client.seed(relative_path=f"dp/{emp.id}/NR12.pdf")

    await run_sync(db_session, client=client, actor="alice@primor.com")
    logs = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.resource == "onedrive_sync.run")
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].actor == "alice@primor.com"
    assert logs[0].action == "sync"


# --------------------------- router ---------------------------------------


@pytest.mark.asyncio
async def test_run_endpoint_requires_auth(api_client: AsyncClient) -> None:
    resp = await api_client.post("/api/v1/onedrive-sync/run", json={"scope": "all"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_run_endpoint_retorna_run_completo(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/onedrive-sync/run",
        json={"scope": "all"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["triggered_by"]  # usuario logado
    assert body["files_scanned"] == 0  # drive mock vazio


@pytest.mark.asyncio
async def test_list_runs_endpoint(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await api_client.post(
        "/api/v1/onedrive-sync/run", json={"scope": "all"}, headers=auth_headers
    )
    resp = await api_client.get("/api/v1/onedrive-sync/runs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_get_run_endpoint_404(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/onedrive-sync/runs/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_run_endpoint_scope_invalido(
    api_client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    resp = await api_client.post(
        "/api/v1/onedrive-sync/run",
        json={"scope": "lixo"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
