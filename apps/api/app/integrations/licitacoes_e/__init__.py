"""Licitacoes-e (Banco do Brasil) edital fallback adapter."""
from app.integrations.licitacoes_e.client import (
    LicitacoesEClient,
    LicitacoesECredentialsRequired,
)

__all__ = ["LicitacoesEClient", "LicitacoesECredentialsRequired"]
