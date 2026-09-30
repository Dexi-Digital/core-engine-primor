"""Relatorio de % de atendimento da varredura documental (demanda #1).

Regra sob teste: `(ok + vencendo) / total`; `vencido` e `ausente` nao
atendem; total 0 -> percentual None.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.diagnostico.atendimento import compute_atendimento
from app.modules.dp_sesmt.models import Employee
from app.modules.obras.models import Obra


@dataclass
class _F:
    area: str
    entity_type: str
    entity_id: int | None
    entity_label: str
    status: str


def test_regra_vencendo_atende_vencido_nao() -> None:
    findings = [
        _F("dp", "employee", 1, "Joao", "ok"),
        _F("dp", "employee", 1, "Joao", "vencendo"),
        _F("sst", "employee", 1, "Joao", "vencido"),
        _F("sst", "employee", 1, "Joao", "ausente"),
    ]
    r = compute_atendimento(findings)
    g = r["geral"]
    assert (g["total"], g["atendidos"], g["pendentes"]) == (4, 2, 2)
    assert g["pct_atendimento"] == 50.0
    assert g["pct_em_dia"] == 25.0
    # Entidade soma as areas em que aparece.
    [ent] = r["por_entidade"]
    assert ent["areas"] == ["dp", "sst"]
    assert ent["pct_atendimento"] == 50.0
    areas = {a["area"]: a for a in r["por_area"]}
    assert areas["dp"]["pct_atendimento"] == 100.0
    assert areas["dp"]["pct_em_dia"] == 50.0
    assert areas["sst"]["pct_atendimento"] == 0.0


def test_por_entidade_ordenado_pior_primeiro_e_empresa_por_cnpj() -> None:
    findings = [
        _F("empresa", "empresa", None, "11.111.111/0001-11", "ok"),
        _F("empresa", "empresa", None, "22.222.222/0001-22", "ausente"),
        _F("frota", "veiculo", 7, "ABC1D23", "ok"),
        _F("frota", "veiculo", 7, "ABC1D23", "vencido"),
    ]
    r = compute_atendimento(findings)
    labels = [e["entity_label"] for e in r["por_entidade"]]
    assert labels == ["22.222.222/0001-22", "ABC1D23", "11.111.111/0001-11"]
    tipos = {t["entity_type"]: t["pct_atendimento"] for t in r["por_tipo_entidade"]}
    assert tipos == {"empresa": 50.0, "veiculo": 50.0}


def test_sem_findings_percentual_none() -> None:
    r = compute_atendimento([])
    assert r["geral"]["total"] == 0
    assert r["geral"]["pct_atendimento"] is None
    assert r["por_entidade"] == []


async def _run(api_client: AsyncClient, headers: dict[str, str]) -> int:
    resp = await api_client.post(
        "/api/v1/diagnostico/run", headers=headers, json={"scope": "all"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_endpoint_atendimento(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    today = date.today()
    db_session.add(
        Employee(
            cpf="111.222.333-44",
            nome_completo="Joao Operario",
            cargo="Pedreiro",
            aso_data=today - timedelta(days=300),
            aso_validade=today + timedelta(days=10),  # vencendo
        )
    )
    db_session.add(Obra(codigo="OBRA-1", nome="Sede", status="ativa"))
    await db_session.commit()
    run_id = await _run(api_client, auth_headers)

    unauth = await api_client.get(f"/api/v1/diagnostico/runs/{run_id}/atendimento")
    assert unauth.status_code == 401

    resp = await api_client.get(
        f"/api/v1/diagnostico/runs/{run_id}/atendimento", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert "vencendo" in body["criterio"]
    g = body["geral"]
    assert g["atendidos"] == g["ok"] + g["vencendo"]
    assert g["vencendo"] >= 1
    tipos = {t["entity_type"] for t in body["por_tipo_entidade"]}
    assert {"employee", "obra"} <= tipos
    emp = next(e for e in body["por_entidade"] if e["entity_type"] == "employee")
    assert "Joao Operario" in emp["entity_label"]
    assert set(emp["areas"]) <= {"dp", "sst"}

    missing = await api_client.get(
        "/api/v1/diagnostico/runs/99999/atendimento", headers=auth_headers
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_csvs_trazem_atendimento(
    api_client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: dict[str, str],
) -> None:
    db_session.add(Obra(codigo="OBRA-1", nome="Sede", status="ativa"))
    await db_session.commit()
    run_id = await _run(api_client, auth_headers)

    export = await api_client.get(
        f"/api/v1/diagnostico/runs/{run_id}/export.csv", headers=auth_headers
    )
    assert export.status_code == 200
    linhas = export.content.decode("utf-8").lstrip("\ufeff").splitlines()
    header = linhas[0].split(";")
    # Colunas novas no fim: planilhas antigas continuam batendo.
    assert header[:2] == ["area", "entity_type"]
    assert header[-2:] == ["pct_atendimento_entidade", "pct_atendimento_run"]
    obra_linha = next(linha for linha in linhas[1:] if ";obra;" in linha)
    # Obra sem nenhum documento -> 0% de atendimento.
    assert obra_linha.split(";")[-2] == "0,0"

    agg = await api_client.get(
        f"/api/v1/diagnostico/runs/{run_id}/atendimento.csv",
        headers=auth_headers,
    )
    assert agg.status_code == 200
    texto = agg.content.decode("utf-8")
    assert texto.startswith("\ufeffnivel;chave;")
    assert "\ngeral;geral;" in texto
    assert "\ncriterio;" in texto
    assert "\narea;obra;" in texto
    assert "\nentidade;obra;" in texto
