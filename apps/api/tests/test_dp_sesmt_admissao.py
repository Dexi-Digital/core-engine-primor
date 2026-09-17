"""Jornada de admissao -- a parte que RECUSA e o que importa testar.

Um teste que so verifica "avancou e virou a proxima etapa" passaria
igual se `avancar()` nao checasse nada. O valor da jornada esta em
barrar o avanco quando falta dado, e em dizer O QUE falta -- entao e
isso que a maioria dos casos aqui exercita.
"""
from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dp_sesmt import admissao as svc
from app.modules.dp_sesmt.models import (
    ADM_CONCLUIDA,
    ADM_DADOS_OK,
    ADM_DOCS_OK,
    ADM_KIT_ENTREGUE,
    ADM_KIT_GERADO,
    ADM_RASCUNHO,
    DOC_EMP_CTPS,
    DOC_EMP_FICHA_REGISTRO,
    Employee,
    EmployeeDocument,
)

pytestmark = pytest.mark.asyncio


async def _employee(
    db: AsyncSession, *, cpf: str = "52998224725", completo: bool = True, obra="Obra A"
) -> Employee:
    """Funcionario com todos os campos do kit, salvo quando `completo=False`.

    `completo=False` deixa o cadastro como ele realmente chega na
    pratica -- nome, CPF e cargo -- que e exatamente o estado em que a
    jornada precisa cobrar o resto.
    """
    emp = Employee(
        cpf=cpf,
        nome_completo="Fulano de Tal",
        cargo="Pedreiro",
        obra=obra,
        data_admissao=date(2026, 3, 2),
    )
    if completo:
        emp.data_nascimento = date(1990, 5, 10)
        emp.rg = "MG-12.345.678"
        emp.pis_pasep = "120.12345.67-8"
        emp.ctps_numero = "1234567"
        emp.ctps_serie = "0001"
        emp.nome_mae = "Maria de Tal"
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    return emp


async def _anexar_docs(db: AsyncSession, emp: Employee) -> None:
    for tipo in (DOC_EMP_CTPS, DOC_EMP_FICHA_REGISTRO):
        db.add(EmployeeDocument(employee_id=emp.id, tipo=tipo))
    await db.commit()


# --- Pendencias -------------------------------------------------------------


async def test_pendencias_listam_campos_e_documentos_faltantes(
    db_session: AsyncSession,
) -> None:
    emp = await _employee(db_session, completo=False)
    pend = await svc.calcular_pendencias(db_session, emp)

    assert "RG" in pend.campos
    assert "PIS/PASEP" in pend.campos
    assert "Nome da mae" in pend.campos
    # Cargo e nome vieram preenchidos -- nao podem aparecer como falta.
    assert "Cargo" not in pend.campos
    assert set(pend.documentos) == {DOC_EMP_CTPS, DOC_EMP_FICHA_REGISTRO}
    assert not pend.vazio


async def test_pendencias_somem_quando_o_cadastro_e_completado(
    db_session: AsyncSession,
) -> None:
    """Pendencia e calculada, nao armazenada.

    Se fosse coluna, anexar o documento deixaria o sistema cobrando
    algo que ja foi entregue -- e a pessoa aprenderia a ignorar o
    alerta.
    """
    emp = await _employee(db_session)
    await _anexar_docs(db_session, emp)
    pend = await svc.calcular_pendencias(db_session, emp)
    assert pend.vazio, pend.como_lista()


# --- Maquina de estados -----------------------------------------------------


async def test_iniciar_e_idempotente(db_session: AsyncSession) -> None:
    emp = await _employee(db_session)
    primeira = await svc.iniciar(db_session, emp)
    segunda = await svc.iniciar(db_session, emp)
    assert primeira.id == segunda.id
    assert segunda.etapa == ADM_RASCUNHO


async def test_avancar_recusa_quando_faltam_campos(
    db_session: AsyncSession,
) -> None:
    emp = await _employee(db_session, completo=False)
    jornada = await svc.iniciar(db_session, emp)

    with pytest.raises(svc.RequisitosNaoAtendidosError) as exc:
        await svc.avancar(db_session, jornada)

    assert exc.value.etapa_alvo == ADM_DADOS_OK
    assert any("RG" in p for p in exc.value.pendencias)
    # E, principalmente: NAO avancou.
    assert jornada.etapa == ADM_RASCUNHO


async def test_avancar_recusa_quando_faltam_documentos(
    db_session: AsyncSession,
) -> None:
    emp = await _employee(db_session)  # dados completos, sem documentos
    jornada = await svc.iniciar(db_session, emp)
    jornada = await svc.avancar(db_session, jornada)
    assert jornada.etapa == ADM_DADOS_OK

    with pytest.raises(svc.RequisitosNaoAtendidosError) as exc:
        await svc.avancar(db_session, jornada)
    assert exc.value.etapa_alvo == ADM_DOCS_OK
    assert DOC_EMP_CTPS in " ".join(exc.value.pendencias)


async def test_kit_gerado_nao_se_marca_a_mao(db_session: AsyncSession) -> None:
    """Etapa de artefato exige o artefato.

    Deixar `avancar()` carimbar `kit_gerado` sem CSV nenhum recriaria o
    problema original: um status que nao corresponde a nada no mundo.
    """
    emp = await _employee(db_session)
    await _anexar_docs(db_session, emp)
    jornada = await svc.iniciar(db_session, emp)
    jornada = await svc.avancar(db_session, jornada)  # dados_ok
    jornada = await svc.avancar(db_session, jornada)  # documentos_ok

    with pytest.raises(svc.AdmissaoError, match="gerar_kit"):
        await svc.avancar(db_session, jornada)


async def test_jornada_completa_ate_concluida(db_session: AsyncSession) -> None:
    emp = await _employee(db_session)
    await _anexar_docs(db_session, emp)
    jornada = await svc.iniciar(db_session, emp)
    jornada = await svc.avancar(db_session, jornada)
    jornada = await svc.avancar(db_session, jornada)

    jornada, conteudo = await svc.gerar_kit(db_session, jornada)
    assert jornada.etapa == ADM_KIT_GERADO
    assert jornada.kit_gerado_em is not None
    assert b"Fulano de Tal" in conteudo

    jornada = await svc.avancar(
        db_session, jornada, entregue_para="contabilidade@zag.com.br"
    )
    assert jornada.etapa == ADM_KIT_ENTREGUE
    assert jornada.kit_entregue_para == "contabilidade@zag.com.br"

    jornada = await svc.avancar(db_session, jornada, actor="rodrigo@zag.com.br")
    assert jornada.etapa == ADM_CONCLUIDA
    assert jornada.confirmado_por == "rodrigo@zag.com.br"

    with pytest.raises(svc.AdmissaoError, match="concluida"):
        await svc.avancar(db_session, jornada)


async def test_cancelar_guarda_o_motivo(db_session: AsyncSession) -> None:
    emp = await _employee(db_session)
    jornada = await svc.iniciar(db_session, emp)
    jornada = await svc.cancelar(db_session, jornada, motivo="reprovado no ASO")
    assert jornada.observacoes == "reprovado no ASO"
    with pytest.raises(svc.AdmissaoError, match="cancelada"):
        await svc.avancar(db_session, jornada)


# --- Kit -------------------------------------------------------------------


async def test_kit_csv_abre_no_excel_pt_br(db_session: AsyncSession) -> None:
    """BOM + `;`: sem os dois, o Excel em pt-BR mostra tudo numa coluna
    so e acentos quebrados -- e o arquivo volta para nos como bug."""
    emp = await _employee(db_session)
    await _anexar_docs(db_session, emp)
    jornada = await svc.iniciar(db_session, emp)
    jornada = await svc.avancar(db_session, jornada)
    jornada = await svc.avancar(db_session, jornada)
    _, conteudo = await svc.gerar_kit(db_session, jornada)

    assert conteudo.startswith(b"\xef\xbb\xbf")
    cabecalho = conteudo.decode("utf-8-sig").splitlines()[0]
    assert ";" in cabecalho
    assert "CPF" in cabecalho


async def test_kit_em_lote_por_obra_pula_quem_tem_pendencia(
    db_session: AsyncSession,
) -> None:
    """O caso que a sazonalidade impoe -- e o que ele NAO pode esconder.

    Uma leva de admissoes na abertura de obra costuma ter alguem com
    documento faltando. Incluir essa pessoa no CSV mandaria admissao
    incompleta para a contabilidade; omiti-la em silencio faria a
    equipe achar que a obra inteira saiu.
    """
    pronto_a = await _employee(db_session, cpf="52998224725", obra="Obra A")
    pronto_b = await _employee(db_session, cpf="11144477735", obra="Obra A")
    incompleto = await _employee(
        db_session, cpf="12345678909", completo=False, obra="Obra A"
    )
    outra_obra = await _employee(db_session, cpf="93541134780", obra="Obra B")

    for emp in (pronto_a, pronto_b, outra_obra):
        await _anexar_docs(db_session, emp)

    for emp in (pronto_a, pronto_b, incompleto, outra_obra):
        jornada = await svc.iniciar(db_session, emp)
        # Coloca todos em documentos_ok na marra: o teste e sobre o
        # lote, e a pessoa incompleta precisa chegar la para provar que
        # o lote a barra mesmo assim.
        jornada.etapa = ADM_DOCS_OK
    await db_session.commit()

    incluidas, conteudo, ignoradas = await svc.gerar_kits_da_obra(
        db_session, "Obra A"
    )

    assert len(incluidas) == 2
    assert {j.employee_id for j in incluidas} == {pronto_a.id, pronto_b.id}

    assert len(ignoradas) == 1
    assert ignoradas[0]["employee_id"] == incompleto.id
    assert ignoradas[0]["motivo"]  # diz o porque, nao so que pulou

    texto = conteudo.decode("utf-8-sig")
    assert texto.count("Fulano de Tal") == 2
    # Obra B nao entra no lote da Obra A.
    assert len(texto.splitlines()) == 3  # cabecalho + 2


async def test_download_do_lote_nao_avanca_etapa(
    db_session: AsyncSession,
) -> None:
    """GET nao pode ter efeito colateral.

    Baixar o arquivo de novo avancando a jornada de alguem seria uma
    mudanca de estado invisivel disparada por um refresh de pagina.
    """
    emp = await _employee(db_session)
    await _anexar_docs(db_session, emp)
    jornada = await svc.iniciar(db_session, emp)
    jornada.etapa = ADM_DOCS_OK
    await db_session.commit()
    await svc.gerar_kits_da_obra(db_session, "Obra A")
    # Le depois de um refresh: o SQLite dos testes devolve o timestamp
    # sem tzinfo, e comparar o objeto em memoria com o relido acusaria
    # uma diferenca que nao existe no banco.
    await db_session.refresh(jornada)
    gerado_em = jornada.kit_gerado_em

    linhas = await svc.linhas_do_lote(db_session, "Obra A")
    await db_session.refresh(jornada)

    assert len(linhas) == 1
    assert jornada.etapa == ADM_KIT_GERADO
    assert jornada.kit_gerado_em == gerado_em


# --- Painel -----------------------------------------------------------------


async def test_painel_separa_quem_esta_travado(db_session: AsyncSession) -> None:
    ok = await _employee(db_session, cpf="52998224725")
    await _anexar_docs(db_session, ok)
    travado = await _employee(db_session, cpf="12345678909", completo=False)
    for emp in (ok, travado):
        await svc.iniciar(db_session, emp)

    resultado = await svc.painel(db_session)

    assert resultado["total"] == 2
    assert resultado["por_etapa"][ADM_RASCUNHO] == 2
    travadas = resultado["travadas"]
    assert len(travadas) == 1
    assert travadas[0]["employee_id"] == travado.id
    assert travadas[0]["pendencias"]


# --- HTTP -------------------------------------------------------------------


async def test_onboarding_abre_jornada_e_ja_diz_o_que_falta(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    """O endpoint que era stub.

    Antes respondia `implemented=True, stub=True` e descartava o
    payload. A regressao que importa e ele voltar a nao fazer nada.
    """
    emp = await _employee(db_session, completo=False)

    resp = await api_client.post(
        "/api/v1/dp-sesmt/onboarding",
        json={
            "cpf": emp.cpf,
            "nome_completo": emp.nome_completo,
            "cargo": emp.cargo,
            "data_admissao": "2026-03-02",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["employee_id"] == emp.id
    assert corpo["etapa"] == ADM_RASCUNHO
    assert corpo["pendencias"], "endpoint precisa dizer o que falta"
    assert "stub" not in corpo


async def test_onboarding_de_cpf_nao_cadastrado_explica_o_caminho(
    api_client: AsyncClient, auth_headers: dict
) -> None:
    resp = await api_client.post(
        "/api/v1/dp-sesmt/onboarding",
        json={
            "cpf": "52998224725",
            "nome_completo": "Ninguem",
            "cargo": "Servente",
            "data_admissao": "2026-03-02",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert "employees" in resp.json()["detail"]


async def test_avancar_via_http_devolve_409_com_a_lista(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    """409 com as pendencias no corpo.

    Um 400 generico devolveria a pessoa ao problema de adivinhar o que
    falta -- que e o que a jornada existe para resolver.
    """
    emp = await _employee(db_session, completo=False)
    jornada = await svc.iniciar(db_session, emp)

    resp = await api_client.post(
        f"/api/v1/dp-sesmt/admissao/{jornada.id}/avancar",
        json={},
        headers=auth_headers,
    )

    assert resp.status_code == 409
    detalhe = resp.json()["detail"]
    assert detalhe["erro"] == "requisitos_nao_atendidos"
    assert detalhe["etapa_alvo"] == ADM_DADOS_OK
    assert detalhe["pendencias"]


async def test_kit_via_http_baixa_csv(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    emp = await _employee(db_session)
    await _anexar_docs(db_session, emp)
    jornada = await svc.iniciar(db_session, emp)
    jornada.etapa = ADM_DOCS_OK
    await db_session.commit()

    resp = await api_client.post(
        f"/api/v1/dp-sesmt/admissao/{jornada.id}/kit", headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    assert "Fulano de Tal" in resp.content.decode("utf-8-sig")


async def test_rebaixar_kit_via_get_nao_avanca_etapa(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    """O GET de download e puro.

    A tela expoe o download como link; um link que avanca a jornada
    dispararia sozinho em prefetch do browser e num refresh de pagina.
    """
    emp = await _employee(db_session)
    await _anexar_docs(db_session, emp)
    jornada = await svc.iniciar(db_session, emp)
    jornada.etapa = ADM_DOCS_OK
    await db_session.commit()
    jornada, _ = await svc.gerar_kit(db_session, jornada)
    await db_session.refresh(jornada)
    gerado_em = jornada.kit_gerado_em

    resp = await api_client.get(
        f"/api/v1/dp-sesmt/admissao/{jornada.id}/kit", headers=auth_headers
    )

    assert resp.status_code == 200, resp.text
    assert "Fulano de Tal" in resp.content.decode("utf-8-sig")
    await db_session.refresh(jornada)
    assert jornada.etapa == ADM_KIT_GERADO
    assert jornada.kit_gerado_em == gerado_em


async def test_rebaixar_kit_inexistente_devolve_404(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    emp = await _employee(db_session)
    jornada = await svc.iniciar(db_session, emp)
    resp = await api_client.get(
        f"/api/v1/dp-sesmt/admissao/{jornada.id}/kit", headers=auth_headers
    )
    assert resp.status_code == 404


async def test_painel_via_http_exige_autenticacao(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/dp-sesmt/admissao/painel")
    assert resp.status_code == 401
