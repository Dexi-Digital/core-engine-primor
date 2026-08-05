"""Tests do workflow de triagem do Captador (Squad 1)."""
from __future__ import annotations

import pytest

from app.modules.licitacoes import triagem


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
