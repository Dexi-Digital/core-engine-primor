"""Importacao de cadastro da OnSafety -> `dp_employees`.

Separada do pull diario de proposito: o ADR-001 decidiu que o pull
NUNCA cria funcionario (cadastro e do RH/onboarding). Esta e uma
operacao explicita, e os testes travam as duas garantias que importam:
nao sobrescrever dado do RH, e nao inventar cargo.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.onsafety.client import OnsafetyClient
from app.modules.dp_sesmt.models import Employee
from app.modules.dp_sesmt.onsafety_sync import import_trabalhadores


@pytest.mark.asyncio
async def test_import_cria_funcionarios_com_cargo_e_obra(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    summary = await import_trabalhadores(db_session, client, actor="t@t.com")

    assert summary.lidos > 0
    assert summary.criados > 0
    emps = (await db_session.execute(select(Employee))).scalars().all()
    assert emps
    assert all(e.cargo for e in emps), "cargo e NOT NULL no modelo"
    assert any(e.obra for e in emps)
    assert any(e.matricula for e in emps)


@pytest.mark.asyncio
async def test_import_nao_sobrescreve_dado_do_rh(db_session: AsyncSession):
    """Espelho da OnSafety nao vence o que o RH digitou."""
    client = OnsafetyClient(api_token=None)
    trab = (await client.list_trabalhadores(page=0, size=10))["items"][0]
    db_session.add(
        Employee(
            cpf=trab["cpf"],
            nome_completo="NOME DIGITADO PELO RH",
            cargo="Cargo do RH",
            matricula="RH-001",
        )
    )
    await db_session.commit()

    await import_trabalhadores(db_session, client, actor="t@t.com")

    emp = (
        await db_session.execute(
            select(Employee).where(Employee.cpf == trab["cpf"])
        )
    ).scalar_one()
    assert emp.nome_completo == "NOME DIGITADO PELO RH"
    assert emp.cargo == "Cargo do RH"
    assert emp.matricula == "RH-001"


@pytest.mark.asyncio
async def test_import_completa_apenas_campos_vazios(
    db_session: AsyncSession,
):
    client = OnsafetyClient(api_token=None)
    trab = (await client.list_trabalhadores(page=0, size=10))["items"][0]
    db_session.add(
        Employee(
            cpf=trab["cpf"],
            nome_completo="X",
            cargo="Cargo do RH",
            matricula=None,  # buraco -- a importacao preenche
        )
    )
    await db_session.commit()

    summary = await import_trabalhadores(db_session, client, actor="t@t.com")

    emp = (
        await db_session.execute(
            select(Employee).where(Employee.cpf == trab["cpf"])
        )
    ).scalar_one()
    assert emp.matricula == trab["matricula"]
    assert emp.cargo == "Cargo do RH"
    assert summary.completados >= 1


@pytest.mark.asyncio
async def test_import_e_idempotente(db_session: AsyncSession):
    client = OnsafetyClient(api_token=None)
    s1 = await import_trabalhadores(db_session, client, actor="t@t.com")
    s2 = await import_trabalhadores(db_session, client, actor="t@t.com")

    assert s2.criados == 0
    assert s2.ja_completos >= 1
    emps = (await db_session.execute(select(Employee))).scalars().all()
    assert len(emps) == s1.criados
