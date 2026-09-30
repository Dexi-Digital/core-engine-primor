"""Filtros usados pela tela de detalhe da obra (/obras/[id]).

A tela junta o que ja esta ligado a obra em outros modulos. Ponto e
juridico tinham a FK `obra_id`, mas as listagens nao aceitavam filtrar
por ela -- a tela teria que baixar tudo e filtrar no navegador.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.juridico.models import Processo
from app.modules.obras.models import Obra
from app.modules.ponto.models import PontoLocalTrabalho

pytestmark = pytest.mark.asyncio


async def _duas_obras(db: AsyncSession) -> tuple[Obra, Obra]:
    a = Obra(codigo="z209", nome="Restauracao MG-010")
    b = Obra(codigo="z210", nome="Pavimentacao Av. Cristiano Machado")
    db.add_all([a, b])
    await db.commit()
    await db.refresh(a)
    await db.refresh(b)
    return a, b


async def test_locais_filtra_por_obra(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    a, b = await _duas_obras(db_session)
    db_session.add_all(
        [
            PontoLocalTrabalho(
                tangerino_id=1, nome="Obra 209", nome_normalizado="obra 209",
                codigo_obra="209", obra_id=a.id,
            ),
            PontoLocalTrabalho(
                tangerino_id=2, nome="Obra 209 canteiro 2",
                nome_normalizado="obra 209 canteiro 2",
                codigo_obra="209", obra_id=a.id,
            ),
            PontoLocalTrabalho(
                tangerino_id=3, nome="Obra 210", nome_normalizado="obra 210",
                codigo_obra="210", obra_id=b.id,
            ),
            PontoLocalTrabalho(
                tangerino_id=4, nome="ADM PRIMOR", nome_normalizado="adm primor",
            ),
        ]
    )
    await db_session.commit()

    r = await api_client.get(
        f"/api/v1/ponto/locais?obra_id={a.id}", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert sorted(x["tangerino_id"] for x in corpo) == [1, 2]
    assert all(x["obra_id"] == a.id for x in corpo)

    # Sem o filtro continua devolvendo todos (tela de ponto).
    todos = await api_client.get("/api/v1/ponto/locais", headers=auth_headers)
    assert len(todos.json()) == 4


async def test_locais_de_obra_sem_vinculo_volta_vazio(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    a, _ = await _duas_obras(db_session)
    r = await api_client.get(
        f"/api/v1/ponto/locais?obra_id={a.id}", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json() == []


async def test_processos_filtra_por_obra(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    a, b = await _duas_obras(db_session)
    db_session.add_all(
        [
            Processo(easyjur_id=10, numero_cnj="0001", codigo_obra="209", obra_id=a.id),
            Processo(easyjur_id=11, numero_cnj="0002", codigo_obra="210", obra_id=b.id),
            Processo(easyjur_id=12, numero_cnj="0003"),
        ]
    )
    await db_session.commit()

    r = await api_client.get(
        f"/api/v1/juridico/processos?obra_id={a.id}", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["total"] == 1
    assert [p["numero_cnj"] for p in corpo["data"]] == ["0001"]

    todos = await api_client.get("/api/v1/juridico/processos", headers=auth_headers)
    assert todos.json()["total"] == 3


async def test_obras_busca_por_codigo_ou_nome(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _duas_obras(db_session)
    por_codigo = await api_client.get("/api/v1/obras?busca=Z209")
    assert [o["codigo"] for o in por_codigo.json()] == ["z209"]
    por_nome = await api_client.get("/api/v1/obras?busca=cristiano")
    assert [o["codigo"] for o in por_nome.json()] == ["z210"]
    assert len((await api_client.get("/api/v1/obras?busca=%20")).json()) == 2


async def test_codigo_duplicado_vira_409(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    """A tela mostra o motivo; antes o IntegrityError virava 500."""
    a, _ = await _duas_obras(db_session)
    a_id = a.id  # o rollback do 409 expira a instancia na sessao compartilhada
    r = await api_client.post(
        "/api/v1/obras", json={"codigo": "z209", "nome": "Outra"},
        headers=auth_headers,
    )
    assert r.status_code == 409, r.text
    assert "z209" in r.json()["detail"]

    r = await api_client.put(
        f"/api/v1/obras/{a_id}", json={"codigo": "z210"}, headers=auth_headers
    )
    assert r.status_code == 409, r.text
