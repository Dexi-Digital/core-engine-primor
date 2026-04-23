"""Modulo B - Manutencao, Custos e Frota.

Escopo:
    - OCR de manuscritos em campo (partes diarias).
    - Cruzamento telemetria x notas fiscais (combustivel/locacao).
    - RPA despachante: IPVA, CRLV, certidoes, multas.
    - Integracao com Sistema 90 para apropriacao de custo.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.dp_sesmt.schemas import ModuleStatus

router = APIRouter()


@router.get("/status", response_model=ModuleStatus)
async def status() -> ModuleStatus:
    return ModuleStatus(module="manutencao_frota", implemented=False, stub=True)
