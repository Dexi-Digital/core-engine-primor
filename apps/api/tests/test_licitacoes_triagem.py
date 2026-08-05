"""Tests do workflow de triagem do Captador (Squad 1)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

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
