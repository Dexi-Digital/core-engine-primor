"""Workflow de triagem do Captador de Licitacoes (Squad 1).

Maquina de status vinda do Projeto Tecnico do cliente (23/06/2026):
a analista aprova/rejeita/observa editais captados; a aprovacao dispara
(o processamento da Squad 2: pasta + anexos + planilha orcamentaria).

Este arquivo segue o formato de `certidoes.py`: constantes de dominio +
funcoes de servico async no mesmo modulo.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# --- Maquina de status (secao 5.1 do Projeto Tecnico) ----------------------

STATUS_NOVO_CAPTADO = "novo_captado"
STATUS_EM_ANALISE = "em_analise"
STATUS_APROVADO = "aprovado"
STATUS_REJEITADO = "rejeitado"
STATUS_PROCESSANDO_ANEXOS = "processando_anexos"
STATUS_COMPLETO = "completo"
STATUS_SEM_PLANILHA = "sem_planilha"
STATUS_ERRO_PORTAL = "erro_portal"
STATUS_ERRO_SHAREPOINT = "erro_sharepoint"

STATUS_VALIDOS: frozenset[str] = frozenset(
    {
        STATUS_NOVO_CAPTADO,
        STATUS_EM_ANALISE,
        STATUS_APROVADO,
        STATUS_REJEITADO,
        STATUS_PROCESSANDO_ANEXOS,
        STATUS_COMPLETO,
        STATUS_SEM_PLANILHA,
        STATUS_ERRO_PORTAL,
        STATUS_ERRO_SHAREPOINT,
    }
)

# Decisoes registradas na trilha (`licitacoes_decisoes_triagem.decisao`).
DECISAO_APROVADO = "aprovado"
DECISAO_REJEITADO = "rejeitado"
DECISAO_OBSERVACAO = "observacao"

# De onde se pode ir para onde. `rejeitado` e `completo` sao terminais;
# os `erro_*` e `sem_planilha` permitem reprocessar (Squad 2 re-dispara).
TRANSICOES_VALIDAS: dict[str, frozenset[str]] = {
    STATUS_NOVO_CAPTADO: frozenset(
        {STATUS_EM_ANALISE, STATUS_APROVADO, STATUS_REJEITADO}
    ),
    STATUS_EM_ANALISE: frozenset(
        {STATUS_APROVADO, STATUS_REJEITADO, STATUS_NOVO_CAPTADO}
    ),
    STATUS_APROVADO: frozenset(
        {STATUS_PROCESSANDO_ANEXOS, STATUS_ERRO_PORTAL, STATUS_ERRO_SHAREPOINT}
    ),
    STATUS_PROCESSANDO_ANEXOS: frozenset(
        {
            STATUS_COMPLETO,
            STATUS_SEM_PLANILHA,
            STATUS_ERRO_PORTAL,
            STATUS_ERRO_SHAREPOINT,
        }
    ),
    STATUS_ERRO_PORTAL: frozenset({STATUS_PROCESSANDO_ANEXOS}),
    STATUS_ERRO_SHAREPOINT: frozenset({STATUS_PROCESSANDO_ANEXOS}),
    STATUS_SEM_PLANILHA: frozenset({STATUS_PROCESSANDO_ANEXOS}),
    STATUS_REJEITADO: frozenset(),
    STATUS_COMPLETO: frozenset(),
}


class TransicaoInvalidaError(ValueError):
    """Transicao de status nao permitida pela maquina de triagem."""

    def __init__(self, atual: str, novo: str) -> None:
        self.atual = atual
        self.novo = novo
        super().__init__(
            f"Transicao de triagem invalida: {atual!r} -> {novo!r}"
        )


def validar_transicao(atual: str, novo: str) -> None:
    """Levanta `TransicaoInvalidaError` se `atual -> novo` nao for permitido."""
    if atual not in STATUS_VALIDOS or novo not in STATUS_VALIDOS:
        raise TransicaoInvalidaError(atual, novo)
    if novo not in TRANSICOES_VALIDAS[atual]:
        raise TransicaoInvalidaError(atual, novo)
