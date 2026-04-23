"""Modulo D - Inteligencia de Licitacoes.

Escopo:
    - Scrapers B2G (Conlicitacao, PNCP, diarios oficiais).
    - Analise de saude municipal (TCE, transparencia, inadimplencia).
    - Busca documental de concorrentes (fase de habilitacao).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.dp_sesmt.schemas import ModuleStatus

router = APIRouter()


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="licitacoes", implemented=False, stub=True)
