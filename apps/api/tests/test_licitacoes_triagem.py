"""Tests do workflow de triagem do Captador (Squad 1)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.audit.models import AuditLog
from app.modules.licitacoes import triagem
from app.modules.licitacoes.models import DecisaoTriagem, Licitacao


class TestMaquinaDeStatus:
    def test_status_validos_cobrem_pdf_do_cliente(self) -> None:
        assert frozenset(
            {
                "novo_captado",
                "em_analise",
                "aprovado",
                "rejeitado",
                "processando_anexos",
                "completo",
                "sem_planilha",
                "erro_portal",
                "erro_sharepoint",
            }
        ) == triagem.STATUS_VALIDOS

    @pytest.mark.parametrize(
        ("atual", "novo"),
        [
            ("novo_captado", "em_analise"),
            ("novo_captado", "aprovado"),
            ("novo_captado", "rejeitado"),
            ("em_analise", "aprovado"),
            ("em_analise", "rejeitado"),
            ("em_analise", "novo_captado"),
            ("aprovado", "processando_anexos"),
            ("processando_anexos", "completo"),
            ("processando_anexos", "sem_planilha"),
            ("processando_anexos", "erro_portal"),
            ("processando_anexos", "erro_sharepoint"),
            ("erro_portal", "processando_anexos"),
            ("erro_sharepoint", "processando_anexos"),
            ("sem_planilha", "processando_anexos"),
        ],
    )
    def test_transicoes_permitidas(self, atual: str, novo: str) -> None:
        triagem.validar_transicao(atual, novo)  # nao levanta

    @pytest.mark.parametrize(
        ("atual", "novo"),
        [
            ("aprovado", "rejeitado"),  # aprovado nao pode ser rejeitado depois
            ("rejeitado", "aprovado"),  # rejeitado e terminal
            ("completo", "novo_captado"),  # completo e terminal
            ("novo_captado", "completo"),  # nao pula a aprovacao
            ("novo_captado", "processando_anexos"),
        ],
    )
    def test_transicoes_proibidas(self, atual: str, novo: str) -> None:
        with pytest.raises(triagem.TransicaoInvalidaError) as exc:
            triagem.validar_transicao(atual, novo)
        assert exc.value.atual == atual
        assert exc.value.novo == novo

    def test_status_desconhecido_levanta(self) -> None:
        with pytest.raises(triagem.TransicaoInvalidaError):
            triagem.validar_transicao("banana", "aprovado")
        with pytest.raises(triagem.TransicaoInvalidaError):
            triagem.validar_transicao("novo_captado", "banana")


def _mk_licitacao(external_id: str = "trg-1") -> Licitacao:
    return Licitacao(
        external_id=external_id,
        source="pncp",
        objeto_compra="Pavimentacao asfaltica em vias urbanas",
        uf_sigla="MG",
        municipio_nome="Belo Horizonte",
        modalidade_nome="Pregao Eletronico",
    )


@pytest.mark.asyncio
async def test_licitacao_nasce_novo_captado_e_decisao_persiste(db_session) -> None:
    lic = _mk_licitacao()
    db_session.add(lic)
    await db_session.commit()
    assert lic.status_triagem == "novo_captado"

    db_session.add(
        DecisaoTriagem(
            licitacao_id=lic.id,
            decisao="aprovado",
            observacao=None,
            usuario_email="analista@primor.com",
        )
    )
    await db_session.commit()

    row = await db_session.scalar(
        select(DecisaoTriagem).where(DecisaoTriagem.licitacao_id == lic.id)
    )
    assert row is not None
    assert row.decisao == "aprovado"
    assert row.usuario_email == "analista@primor.com"
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_aprovar_muda_status_grava_decisao_e_audit(db_session) -> None:
    lic = _mk_licitacao("trg-aprovar")
    db_session.add(lic)
    await db_session.commit()

    decisao = await triagem.aprovar(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="dentro do perfil de engenharia",
    )

    assert decisao.decisao == "aprovado"
    await db_session.refresh(lic)
    assert lic.status_triagem == "aprovado"

    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.resource == "licitacoes.triagem")
    )
    assert audit is not None
    assert audit.actor == "analista@primor.com"
    assert audit.action == "triagem.aprovar"
    assert audit.resource_id == str(lic.id)


@pytest.mark.asyncio
async def test_rejeitar_exige_motivo(db_session) -> None:
    lic = _mk_licitacao("trg-rejeitar-sem-motivo")
    db_session.add(lic)
    await db_session.commit()

    with pytest.raises(ValueError, match="motivo"):
        await triagem.rejeitar(
            db_session,
            licitacao_id=lic.id,
            usuario_email="analista@primor.com",
            observacao="   ",
        )
    await db_session.refresh(lic)
    assert lic.status_triagem == "novo_captado"


@pytest.mark.asyncio
async def test_rejeitar_com_motivo_e_terminal(db_session) -> None:
    lic = _mk_licitacao("trg-rejeitar")
    db_session.add(lic)
    await db_session.commit()

    await triagem.rejeitar(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="objeto fora do perfil (merenda escolar)",
    )
    await db_session.refresh(lic)
    assert lic.status_triagem == "rejeitado"

    with pytest.raises(triagem.TransicaoInvalidaError):
        await triagem.aprovar(
            db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
        )


@pytest.mark.asyncio
async def test_observacao_move_para_em_analise(db_session) -> None:
    lic = _mk_licitacao("trg-obs")
    db_session.add(lic)
    await db_session.commit()

    await triagem.registrar_observacao(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="aguardando planilha no portal",
    )
    await db_session.refresh(lic)
    assert lic.status_triagem == "em_analise"

    # segunda observacao nao muda mais o status
    await triagem.registrar_observacao(
        db_session,
        licitacao_id=lic.id,
        usuario_email="analista@primor.com",
        observacao="portal voltou",
    )
    await db_session.refresh(lic)
    assert lic.status_triagem == "em_analise"

    rows = await triagem.listar_decisoes(db_session, licitacao_id=lic.id)
    assert len(rows) == 2
    assert rows[0].observacao == "portal voltou"  # mais recente primeiro


@pytest.mark.asyncio
async def test_aprovar_licitacao_inexistente(db_session) -> None:
    with pytest.raises(LookupError):
        await triagem.aprovar(
            db_session, licitacao_id=99999, usuario_email="a@primor.com"
        )


@pytest.mark.asyncio
async def test_aprovar_dispara_celery_quando_flag_ligada(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("CAPTADOR_AUTO_PROCESS", "1")
    get_settings.cache_clear()

    enviados: list[tuple[str, list, str]] = []

    class FakeDispatcher:
        def send_task(self, name: str, args: list, queue: str) -> None:
            enviados.append((name, args, queue))

    monkeypatch.setattr(triagem, "_get_celery_dispatcher", lambda: FakeDispatcher())

    lic = _mk_licitacao("trg-celery")
    db_session.add(lic)
    await db_session.commit()
    await triagem.aprovar(
        db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
    )

    assert enviados == [
        ("worker.tasks.licitacoes.processar_edital_aprovado", [lic.id], "licitacoes")
    ]
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_aprovar_sobrevive_broker_fora(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem a task da Squad 2 registrada / broker down, aprovar NAO pode falhar."""
    from app.core.config import get_settings

    monkeypatch.setenv("CAPTADOR_AUTO_PROCESS", "1")
    get_settings.cache_clear()

    def _boom() -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(triagem, "_get_celery_dispatcher", _boom)

    lic = _mk_licitacao("trg-broker-down")
    db_session.add(lic)
    await db_session.commit()
    decisao = await triagem.aprovar(
        db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
    )
    assert decisao.decisao == "aprovado"
    await db_session.refresh(lic)
    assert lic.status_triagem == "aprovado"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_aprovar_nao_dispara_celery_por_default(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamado: list[bool] = []
    monkeypatch.setattr(
        triagem, "_get_celery_dispatcher", lambda: chamado.append(True)
    )
    lic = _mk_licitacao("trg-sem-flag")
    db_session.add(lic)
    await db_session.commit()
    await triagem.aprovar(
        db_session, licitacao_id=lic.id, usuario_email="a@primor.com"
    )
    assert chamado == []
