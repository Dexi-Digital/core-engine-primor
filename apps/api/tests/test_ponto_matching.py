"""Extracao do codigo da obra a partir do nome do local de trabalho.

Todos os nomes deste arquivo sao REAIS -- vieram da conta da Primor no
Solides em 27/08/2026 (83 locais). Nao invente casos novos sem conferir
contra a lista real.
"""
from __future__ import annotations

import pytest

from app.modules.ponto.matching import codigo_obra_de_local, normalizar_nome_local


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("Obra 243", "243"),
        ("Obra C034", "C034"),
        ("Obra C043", "C043"),
        ("Obra 021 CTC", "021"),
        ("Obra 014 CTC", "014"),
        ("OBRA 252", "252"),
        ("Obra 217 - Januária", "217"),
        ("Obra 247 Januária", "247"),
        ("Obra 245 - Morada Nova de Minas", "245"),
        ("OBRA 246 - ABAETÉ", "246"),
        ("Obra c008 Santa Margarida", "C008"),
        ("Obra 240 Juatuba", "240"),
        ("Obra 237 Rio Acima", "237"),
        ("Obra 230 Mateus Leme", "230"),
    ],
)
def test_extrai_codigo_de_locais_de_obra(nome: str, esperado: str):
    assert codigo_obra_de_local(nome) == esperado


@pytest.mark.parametrize(
    "nome",
    [
        "MANUTENÇÃO PRIMOR",
        "ADM PRIMOR",
        "ADM GXM",
        "ADM CTC",
        "ADM-ZAG",
        "ESCRITÓRIO PRIMOR",
        "GUAXIMA/BANDEIRANTES",
        "CONSORCIO CTC/DIFFERENCIAL",
        "CIRRUS",
    ],
)
def test_locais_administrativos_nao_viram_obra(nome: str):
    """Administrativo e consorcio nao sao obra -- ficam sem vinculo, e
    isso e correto, nao falha."""
    assert codigo_obra_de_local(nome) is None


def test_nome_vazio_ou_nulo():
    assert codigo_obra_de_local("") is None
    assert codigo_obra_de_local(None) is None


def test_normalizacao_de_nome_para_deduplicacao():
    """"Obra 010 CTC" aparece DUAS vezes na conta real -- a normalizacao
    e o que permite detectar a duplicidade."""
    assert normalizar_nome_local("Obra 010 CTC") == normalizar_nome_local(
        "  obra   010   ctc  "
    )
    assert normalizar_nome_local("OBRA 246 - ABAETÉ") == normalizar_nome_local(
        "Obra 246 - Abaeté"
    )
