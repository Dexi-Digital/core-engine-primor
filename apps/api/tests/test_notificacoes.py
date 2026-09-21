"""Notificacoes na plataforma -- substituem o email dos boletins.

Ate 21/09/2026 todo aviso do sistema saia por email (Resend): boletins
de licitacao, alertas de certidao, ASO e afastamento. O cliente pediu
que o boletim parasse de mandar email e virasse notificacao dentro da
plataforma -- e nao havia nenhuma peca de notificacao no projeto.

Duas regras que os testes protegem:

- **Notificacao e por destinatario.** Uma pessoa nunca ve a de outra.
  Boletim de licitacao pode conter valor de contrato e estrategia
  comercial.
- **Despachar duas vezes nao duplica.** O beat roda 3x/dia sobre a
  mesma saved query; sem chave de idempotencia o sino encheria de
  copias do mesmo boletim.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notificacoes import service as svc
from app.modules.notificacoes.models import Notificacao

pytestmark = pytest.mark.asyncio


async def _criar(db: AsyncSession, *, destinatario: str, titulo="Titulo", **kw):
    return await svc.criar_notificacao(
        db,
        destinatario=destinatario,
        titulo=titulo,
        corpo=kw.pop("corpo", "Corpo"),
        categoria=kw.pop("categoria", "licitacoes"),
        link=kw.pop("link", None),
        chave_idempotencia=kw.pop("chave_idempotencia", None),
    )


async def test_notificacao_nasce_nao_lida(db_session: AsyncSession) -> None:
    n = await _criar(db_session, destinatario="a@x.com")
    assert n.lida_em is None


async def test_cada_um_ve_so_a_sua(db_session: AsyncSession) -> None:
    """Boletim carrega valor de contrato e alvo comercial.

    Vazar a notificacao de um usuario para outro seria vazar
    estrategia de concorrencia -- por isso a consulta e SEMPRE por
    destinatario, nunca uma listagem global filtrada na tela.
    """
    await _criar(db_session, destinatario="a@x.com", titulo="Da A")
    await _criar(db_session, destinatario="b@x.com", titulo="Da B")

    da_a = await svc.listar(db_session, destinatario="a@x.com")
    assert [n.titulo for n in da_a] == ["Da A"]


async def test_mesma_chave_nao_duplica(db_session: AsyncSession) -> None:
    """O beat roda 3x/dia sobre a mesma saved query.

    Sem isso o sino acumularia tres copias do mesmo boletim por dia --
    e notificacao repetida ensina a ignorar notificacao.
    """
    for _ in range(3):
        await _criar(
            db_session,
            destinatario="a@x.com",
            chave_idempotencia="boletim:7:2026-09-21",
        )
    assert len(await svc.listar(db_session, destinatario="a@x.com")) == 1


async def test_chaves_diferentes_criam_duas(db_session: AsyncSession) -> None:
    await _criar(db_session, destinatario="a@x.com", chave_idempotencia="b:1")
    await _criar(db_session, destinatario="a@x.com", chave_idempotencia="b:2")
    assert len(await svc.listar(db_session, destinatario="a@x.com")) == 2


async def test_sem_chave_nao_deduplica(db_session: AsyncSession) -> None:
    """Chave e opcional: aviso avulso pode repetir de proposito."""
    await _criar(db_session, destinatario="a@x.com")
    await _criar(db_session, destinatario="a@x.com")
    assert len(await svc.listar(db_session, destinatario="a@x.com")) == 2


async def test_marcar_como_lida(db_session: AsyncSession) -> None:
    n = await _criar(db_session, destinatario="a@x.com")
    await svc.marcar_lida(db_session, destinatario="a@x.com", notificacao_id=n.id)
    assert (await db_session.get(Notificacao, n.id)).lida_em is not None


async def test_nao_marco_a_notificacao_de_outro(db_session: AsyncSession) -> None:
    """Sem o filtro de destinatario, o id sozinho daria acesso."""
    n = await _criar(db_session, destinatario="b@x.com")
    ok = await svc.marcar_lida(
        db_session, destinatario="a@x.com", notificacao_id=n.id
    )
    assert ok is False
    assert (await db_session.get(Notificacao, n.id)).lida_em is None


async def test_contador_conta_so_nao_lidas(db_session: AsyncSession) -> None:
    a = await _criar(db_session, destinatario="a@x.com")
    await _criar(db_session, destinatario="a@x.com")
    await _criar(db_session, destinatario="b@x.com")

    assert await svc.contar_nao_lidas(db_session, destinatario="a@x.com") == 2
    await svc.marcar_lida(db_session, destinatario="a@x.com", notificacao_id=a.id)
    assert await svc.contar_nao_lidas(db_session, destinatario="a@x.com") == 1


async def test_api_lista_apenas_do_usuario_logado(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    await _criar(db_session, destinatario="test-admin@primor.com", titulo="Minha")
    await _criar(db_session, destinatario="outro@x.com", titulo="De outro")

    r = await api_client.get("/api/v1/notificacoes", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert [n["titulo"] for n in r.json()["data"]] == ["Minha"]


async def test_api_contador(
    api_client: AsyncClient, auth_headers: dict, db_session: AsyncSession
) -> None:
    await _criar(db_session, destinatario="test-admin@primor.com")
    r = await api_client.get(
        "/api/v1/notificacoes/nao-lidas", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


async def test_api_exige_autenticacao(api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/notificacoes")).status_code == 401
