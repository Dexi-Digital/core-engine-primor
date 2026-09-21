"""Home = gargalos por frente, nao relatorio do que o sistema fez.

Pedido de 21/09/2026: a home mostrava "Integracoes ativas" (tecnico) e
um feed de automacoes -- contava o que o sistema estava fazendo, nao
onde a operacao esta travada. Este endpoint responde a segunda
pergunta: por frente, o que precisa de alguem hoje.

Regra: gargalo so aparece com quantidade > 0, e cada um leva o link da
tela que resolve. Frente sem gargalo diz "nada pendente" -- e isso e
informacao, nao ausencia.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dp_sesmt.models import ADM_DOCS_OK, AdmissaoJornada, Employee
from app.modules.financeiro_contratos.models import Contrato
from app.modules.licitacoes.models import CertidaoEmpresa, Licitacao
from app.modules.overview import gargalos as svc
from app.modules.ponto.models import PontoLocalTrabalho

pytestmark = pytest.mark.asyncio


def _por_titulo(resp: dict, frente: str) -> dict[str, dict]:
    bloco = next(f for f in resp["frentes"] if f["chave"] == frente)
    return {g["chave"]: g for g in bloco["gargalos"]}


async def test_frente_sem_gargalo_e_explicita(db_session: AsyncSession) -> None:
    resp = await svc.gargalos(db_session)
    financeiro = next(f for f in resp["frentes"] if f["chave"] == "financeiro")
    assert financeiro["gargalos"] == []
    assert resp["total"] == 0


async def test_contrato_vencido_e_gargalo_do_financeiro(db_session: AsyncSession) -> None:
    db_session.add(Contrato(
        titulo="Locacao", contraparte_nome="X", tipo="locacao",
        valor=Decimal("1"), data_inicio=date(2025, 1, 1),
        data_fim=date.today() - timedelta(days=3), status="vigente",
    ))
    db_session.add(Contrato(
        titulo="Vence logo", contraparte_nome="Y", tipo="locacao",
        valor=Decimal("1"), data_inicio=date(2025, 1, 1),
        data_fim=date.today() + timedelta(days=10), status="vigente",
    ))
    await db_session.commit()
    g = _por_titulo(await svc.gargalos(db_session), "financeiro")
    assert g["contratos_vencidos"]["quantidade"] == 1
    assert g["contratos_vencidos"]["gravidade"] == "critico"
    assert g["contratos_vencendo"]["quantidade"] == 1
    assert g["contratos_vencidos"]["link"].startswith("/financeiro")


async def test_aso_vencido_e_admissao_travada_sao_gargalos_do_rh(
    db_session: AsyncSession,
) -> None:
    emp = Employee(cpf="52998224725", nome_completo="A", cargo="Pedreiro",
                   obra="Obra A", status="ativo",
                   aso_validade=date.today() - timedelta(days=1))
    db_session.add(emp)
    await db_session.flush()
    db_session.add(AdmissaoJornada(employee_id=emp.id, obra="Obra A", etapa=ADM_DOCS_OK))
    await db_session.commit()
    g = _por_titulo(await svc.gargalos(db_session), "rh")
    assert g["aso_vencido"]["quantidade"] == 1
    assert g["admissoes_em_aberto"]["quantidade"] == 1


async def test_certidao_vencida_e_gargalo_de_licitacoes(db_session: AsyncSession) -> None:
    db_session.add(CertidaoEmpresa(
        empresa_cnpj="44229813000123", tipo="CND_FEDERAL",
        validade=date.today() - timedelta(days=1),
    ))
    await db_session.commit()
    g = _por_titulo(await svc.gargalos(db_session), "licitacoes")
    assert g["certidoes_vencidas"]["quantidade"] == 1
    assert g["certidoes_vencidas"]["gravidade"] == "critico"


async def test_captacao_parada_e_gargalo_de_licitacoes(db_session: AsyncSession) -> None:
    """A oportunidade mais nova ha 150 dias nao e "sem novidade": e
    captacao parada. Foi exatamente o que a tela mostrou em 21/09."""
    db_session.add(Licitacao(
        external_id="x-1", source="pncp", objeto_compra="Obra",
        data_publicacao_pncp=datetime.now() - timedelta(days=150),
    ))
    await db_session.commit()
    g = _por_titulo(await svc.gargalos(db_session), "licitacoes")
    assert g["captacao_parada"]["quantidade"] >= 149
    assert "dia" in g["captacao_parada"]["titulo"]


async def test_captacao_recente_nao_e_gargalo(db_session: AsyncSession) -> None:
    db_session.add(Licitacao(
        external_id="x-2", source="pncp", objeto_compra="Obra",
        data_publicacao_pncp=datetime.now() - timedelta(days=1),
    ))
    await db_session.commit()
    assert "captacao_parada" not in _por_titulo(await svc.gargalos(db_session), "licitacoes")


async def test_local_do_ponto_sem_obra_e_gargalo_de_obras(db_session: AsyncSession) -> None:
    db_session.add(PontoLocalTrabalho(
        tangerino_id=1, nome="Obra 231 Januaria", nome_normalizado="obra 231 januaria",
        codigo_obra="231", obra_id=None,
    ))
    db_session.add(PontoLocalTrabalho(
        tangerino_id=2, nome="ADM PRIMOR", nome_normalizado="adm primor",
        codigo_obra=None, obra_id=None,
    ))
    await db_session.commit()
    g = _por_titulo(await svc.gargalos(db_session), "obras")
    # so o local COM codigo conta: administrativo nao tem obra por definicao
    assert g["obras_nao_cadastradas"]["quantidade"] == 1


async def test_api_devolve_gargalos(api_client: AsyncClient, auth_headers: dict) -> None:
    r = await api_client.get("/api/v1/overview/gargalos", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert {f["chave"] for f in r.json()["frentes"]} >= {
        "rh", "frota", "financeiro", "licitacoes", "juridico", "obras",
    }


async def test_api_exige_autenticacao(api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/overview/gargalos")).status_code == 401
